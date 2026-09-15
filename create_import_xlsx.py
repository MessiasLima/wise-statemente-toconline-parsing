#!/usr/bin/env python3

import csv
import sys
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
X14AC = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
NS = {"main": MAIN}

for prefix, uri in {
    "": MAIN,
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "x14ac": X14AC,
    "xr": "http://schemas.microsoft.com/office/spreadsheetml/2014/revision",
    "xr2": "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2",
    "xr3": "http://schemas.microsoft.com/office/spreadsheetml/2016/revision3",
}.items():
    ET.register_namespace(prefix, uri)


def qname(local_name):
    return f"{{{MAIN}}}{local_name}"


def excel_date(value):
    parsed = datetime.strptime(value, "%d-%m-%Y").date()
    return str((parsed - date(1899, 12, 30)).days)


def money(value):
    return format(Decimal(value).quantize(Decimal("0.01")), "f")


def description(row):
    value = row["Description"]
    reference = row.get("Payment Reference") or ""
    if reference and reference not in value:
        value += f" [Ref: {reference}]"
    return value


def cell(reference, style, value=None, shared_string=False):
    attributes = {"r": reference, "s": str(style)}
    if shared_string:
        attributes["t"] = "s"
    element = ET.Element(qname("c"), attributes)
    if value is not None:
        value_element = ET.SubElement(element, qname("v"))
        value_element.text = str(value)
    return element


ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
TEMPLATE_PATH = ROOT / "importacao_movimentos.xlsx"
ALLOWED_ROOT_FILES = {
    "create_import_xlsx.py",
    "importacao_movimentos.xlsx",
    ".gitignore",
    "README.md",
}


def fail_security(message):
    raise SystemExit(f"Security check failed: {message}")


def check_directory_contents(directory, extension):
    if not directory.is_dir() or directory.is_symlink():
        fail_security(f"{directory.name}/ must be a real directory")

    for entry in directory.iterdir():
        if entry.name == ".gitkeep":
            if not entry.is_file() or entry.is_symlink():
                fail_security(f"{entry} must be a regular file")
            continue
        if entry.is_symlink() or not entry.is_file():
            fail_security(f"unexpected entry in {directory.name}/: {entry.name}")
        if entry.suffix.lower() != extension:
            fail_security(f"unexpected file in {directory.name}/: {entry.name}")


def security_check():
    if not ROOT.is_dir():
        fail_security("repository root is missing")

    for entry in ROOT.iterdir():
        if entry.name in ALLOWED_ROOT_FILES:
            if entry.is_symlink() or not entry.is_file():
                fail_security(f"{entry.name} must be a regular file")
        elif entry.name == ".git":
            if entry.is_symlink() or not entry.is_dir():
                fail_security(".git must be a real directory")
        elif entry.name in {"input", "output"}:
            if entry.is_symlink() or not entry.is_dir():
                fail_security(f"{entry.name}/ must be a real directory")
        else:
            fail_security(f"unexpected repository entry: {entry.name}")

    check_directory_contents(INPUT_DIR, ".csv")
    check_directory_contents(OUTPUT_DIR, ".xlsx")


def convert_file(template_path, source_path, output_path):
    required = {
        "Date",
        "Amount",
        "Description",
        "Payment Reference",
        "Running Balance",
        "Transaction Type",
    }

    with source_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.DictReader(source_file)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"Missing source columns: {', '.join(sorted(missing))}")
        rows = list(reader)

    if not rows:
        raise SystemExit("The source CSV contains no transactions")

    # Wise exports newest transactions first; the template represents a statement period.
    rows.reverse()

    opening = Decimal(rows[0]["Running Balance"]) - Decimal(rows[0]["Amount"])
    closing = Decimal(rows[-1]["Running Balance"])
    total = sum((Decimal(row["Amount"]) for row in rows), Decimal("0"))
    if (opening + total).quantize(Decimal("0.01")) != closing.quantize(Decimal("0.01")):
        raise SystemExit("Source running balances do not reconcile")

    with zipfile.ZipFile(template_path, "r") as template_zip:
        parts = {info.filename: template_zip.read(info.filename) for info in template_zip.infolist()}

    shared_root = ET.fromstring(parts["xl/sharedStrings.xml"])
    shared_items = shared_root.findall(qname("si"))
    shared_indices = {}
    for index, item in enumerate(shared_items):
        text = "".join(item.itertext())
        if text not in shared_indices:
            shared_indices[text] = index

    descriptions = [description(row) for row in rows]
    for value in descriptions:
        if value in shared_indices:
            continue
        item = ET.SubElement(shared_root, qname("si"))
        text = ET.SubElement(item, qname("t"))
        if value[:1].isspace() or value[-1:].isspace():
            text.set(f"{{{XML}}}space", "preserve")
        text.text = value
        shared_indices[value] = len(shared_items)
        shared_items.append(item)

    count = int(shared_root.get("count", "0")) + len(rows)
    shared_root.set("count", str(count))
    shared_root.set("uniqueCount", str(len(shared_items)))
    parts["xl/sharedStrings.xml"] = ET.tostring(shared_root, encoding="utf-8", xml_declaration=True)

    sheet_root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    sheet_data = sheet_root.find(qname("sheetData"))
    if sheet_data is None:
        raise SystemExit("Template has no worksheet data")

    for reference, value in [("D3", money(opening)), ("D5", money(closing))]:
        balance_cell = sheet_root.find(f".//main:c[@r='{reference}']", NS)
        if balance_cell is None:
            raise SystemExit(f"Template is missing {reference}")
        balance_value = balance_cell.find(qname("v"))
        if balance_value is None:
            balance_value = ET.SubElement(balance_cell, qname("v"))
        balance_value.text = value

    for row_element in list(sheet_data):
        if int(row_element.get("r", "0")) >= 8:
            sheet_data.remove(row_element)

    for offset, row in enumerate(rows, start=8):
        row_element = ET.SubElement(
            sheet_data,
            qname("row"),
            {
                "r": str(offset),
                "spans": "1:21",
                "ht": "17",
                "customHeight": "1",
                f"{{{X14AC}}}dyDescent": "0.2",
            },
        )
        row_element.extend(
            [
                cell(f"A{offset}", 2, excel_date(row["Date"])),
                cell(f"B{offset}", 2, excel_date(row["Date"])),
                cell(f"C{offset}", 3, shared_indices[description(row)], shared_string=True),
                cell(f"D{offset}", 4, money(row["Amount"])),
                cell(f"E{offset}", 12),
            ]
        )

    dimension = sheet_root.find(qname("dimension"))
    if dimension is not None:
        dimension.set("ref", f"A1:U{7 + len(rows)}")

    formula_cell = sheet_root.find(".//main:c[@r='E1']", NS)
    if formula_cell is not None:
        cached_value = formula_cell.find(qname("v"))
        if cached_value is not None:
            cached_value.text = (
                "Saldos e movimentos validados- os saldos inicial e final são coerentes "
                "com os movimentos inseridos"
            )

    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet_root, encoding="utf-8", xml_declaration=True)

    workbook_root = ET.fromstring(parts["xl/workbook.xml"])
    calc_pr = workbook_root.find(qname("calcPr"))
    if calc_pr is not None:
        calc_pr.set("fullCalcOnLoad", "1")
        calc_pr.set("forceFullCalc", "1")
    parts["xl/workbook.xml"] = ET.tostring(workbook_root, encoding="utf-8", xml_declaration=True)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as output_zip:
        for name, content in parts.items():
            output_zip.writestr(name, content)

    print(f"Created {output_path} with {len(rows)} transactions")
    print(f"Opening balance: {money(opening)}")
    print(f"Closing balance: {money(closing)}")


def main():
    if len(sys.argv) != 1:
        raise SystemExit("Usage: python3 create_import_xlsx.py")

    security_check()
    source_paths = sorted(
        path for path in INPUT_DIR.iterdir() if path.is_file() and path.suffix.lower() == ".csv"
    )
    if not source_paths:
        raise SystemExit("No CSV files found in input/")

    output_paths = {}
    for source_path in source_paths:
        output_path = OUTPUT_DIR / f"{source_path.stem}.xlsx"
        previous = output_paths.setdefault(output_path.name, source_path.name)
        if previous != source_path.name:
            raise SystemExit(
                f"Multiple input files would create {output_path.name}: {previous}, {source_path.name}"
            )

    for source_path in source_paths:
        output_path = OUTPUT_DIR / f"{source_path.stem}.xlsx"
        convert_file(TEMPLATE_PATH, source_path, output_path)


if __name__ == "__main__":
    main()
