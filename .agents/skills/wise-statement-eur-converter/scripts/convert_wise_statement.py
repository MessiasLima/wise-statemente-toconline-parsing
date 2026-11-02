#!/usr/bin/env python3
"""Create a non-official EUR translation of a Wise GBP statement CSV."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable


CENT = Decimal("0.01")
REQUIRED_COLUMNS = {
    "TransferWise ID",
    "Date",
    "Date Time",
    "Amount",
    "Currency",
    "Running Balance",
    "Exchange From",
    "Exchange To",
    "Exchange Rate",
    "Total fees",
    "Exchange To Amount",
}


class ConversionError(Exception):
    """Raised when the statement or reference rates are ambiguous or invalid."""


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_decimal(value: str | None, *, field: str, row_number: int, required: bool = False) -> Decimal:
    if value is None or not value.strip():
        if required:
            raise ConversionError(f"Row {row_number}: {field} is missing")
        return Decimal("0")
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ConversionError(f"Row {row_number}: invalid {field} value {value!r}") from exc
    if not result.is_finite():
        raise ConversionError(f"Row {row_number}: non-finite {field} value {value!r}")
    return result


def parse_date(value: str, *, row_number: int) -> date:
    try:
        return datetime.strptime(value, "%d-%m-%Y").date()
    except (TypeError, ValueError) as exc:
        raise ConversionError(f"Row {row_number}: expected Date in DD-MM-YYYY format, got {value!r}") from exc


def parse_timestamp(value: str, *, row_number: int) -> datetime:
    for pattern in ("%d-%m-%Y %H:%M:%S.%f", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern)
        except (TypeError, ValueError):
            pass
    raise ConversionError(f"Row {row_number}: invalid Date Time value {value!r}")


def source_opening_balance(rows: list[dict[str, str]]) -> Decimal:
    chronological = sorted(
        enumerate(rows, start=2),
        key=lambda indexed_row: (
            parse_timestamp(indexed_row[1].get("Date Time", ""), row_number=indexed_row[0]),
            indexed_row[0],
        ),
    )
    first_row_number, first_row = chronological[0]
    return parse_decimal(
        first_row.get("Running Balance"),
        field="Running Balance",
        row_number=first_row_number,
        required=True,
    ) - parse_decimal(first_row.get("Amount"), field="Amount", row_number=first_row_number, required=True)


def validate_source_running_balances(rows: list[dict[str, str]]) -> Decimal:
    """Check the source GBP ledger before translating; don't hide corrupt input."""
    chronological = sorted(
        enumerate(rows, start=2),
        key=lambda indexed_row: (parse_timestamp(indexed_row[1].get("Date Time", ""), row_number=indexed_row[0]), indexed_row[0]),
    )
    opening_balance = source_opening_balance(rows)
    expected_balance = opening_balance

    for row_number, row in chronological:
        if row.get("Currency", "").strip() != "GBP":
            raise ConversionError(
                f"Row {row_number}: source ledger currency must be GBP, found {row.get('Currency')!r}"
            )
        expected_balance += parse_decimal(row.get("Amount"), field="Amount", row_number=row_number, required=True)
        stated_balance = parse_decimal(
            row.get("Running Balance"), field="Running Balance", row_number=row_number, required=True
        )
        if money(expected_balance) != money(stated_balance):
            raise ConversionError(
                f"Source balance check failed at CSV row {row_number} ({row.get('TransferWise ID')}): "
                f"expected GBP {money(expected_balance):.2f}, found GBP {money(stated_balance):.2f}. "
                "Check that the export is complete and transaction timestamps/order are valid before converting."
            )
    return opening_balance


def wise_gbp_eur_rate(row: dict[str, str], *, row_number: int) -> Decimal | None:
    if row.get("Exchange From", "").strip() != "GBP" or row.get("Exchange To", "").strip() != "EUR":
        return None
    text = row.get("Exchange Rate", "").strip()
    if not text:
        return None
    rate = parse_decimal(text, field="Exchange Rate", row_number=row_number, required=True)
    if rate <= 0:
        raise ConversionError(f"Row {row_number}: Wise GBP→EUR rate must be positive")
    return rate


def parse_rate_override(value: str) -> tuple[date, Decimal]:
    day_text, separator, rate_text = value.partition("=")
    if not separator:
        raise ConversionError(f"Invalid --rate {value!r}; expected DD-MM-YYYY=EUR_PER_GBP")
    try:
        rate_day = datetime.strptime(day_text.strip(), "%d-%m-%Y").date()
        rate = Decimal(rate_text.strip())
    except (ValueError, InvalidOperation) as exc:
        raise ConversionError(f"Invalid --rate {value!r}; expected DD-MM-YYYY=EUR_PER_GBP") from exc
    if not rate.is_finite() or rate <= 0:
        raise ConversionError(f"Invalid --rate {value!r}; rate must be a positive EUR-per-GBP value")
    return rate_day, rate


def rate_for_row(
    row: dict[str, str],
    user_rates: dict[date, Decimal],
    *,
    row_number: int,
) -> tuple[Decimal, bool]:
    """Return EUR per GBP and whether it came from a user-provided daily rate."""
    wise_rate = wise_gbp_eur_rate(row, row_number=row_number)
    if wise_rate is not None:
        return wise_rate, False
    transaction_date = parse_date(row.get("Date", ""), row_number=row_number)
    if transaction_date not in user_rates:
        raise ConversionError(
            f"Missing user-provided Wise GBP→EUR rate for {transaction_date.strftime('%d-%m-%Y')}; "
            "ask the user for the EUR-per-GBP rate for this date before converting"
        )
    return user_rates[transaction_date], True


def missing_rate_dates(rows: list[dict[str, str]]) -> list[date]:
    needed: set[date] = set()
    for row_number, row in enumerate(rows, start=2):
        if wise_gbp_eur_rate(row, row_number=row_number) is None:
            needed.add(parse_date(row.get("Date", ""), row_number=row_number))
    return sorted(needed)


def append_rate_note(existing_note: str, rate: Decimal, transaction_date: date) -> str:
    annotation = (
        f"EUR conversion rate used: 1 GBP = {rate} EUR "
        f"(user-provided Wise daily rate for {transaction_date.strftime('%d-%m-%Y')})"
    )
    return f"{existing_note} | {annotation}" if existing_note.strip() else annotation


def output_path_for(source: Path) -> Path:
    if "_GBP_" in source.name:
        name = source.name.replace("_GBP_", "_EUR_", 1)
    else:
        name = f"{source.stem}_EUR{source.suffix}"
    return source.with_name(name)


def read_statement(source: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with source.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ConversionError("The input CSV has no header row")
            missing = REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                raise ConversionError(f"The input CSV is missing required columns: {', '.join(sorted(missing))}")
            rows = list(reader)
            if any(None in row or any(value is None for value in row.values()) for row in rows):
                raise ConversionError("At least one CSV row has a different number of fields than its header")
            if not rows:
                raise ConversionError("The input CSV contains no transaction rows")
            return reader.fieldnames, rows
    except OSError as exc:
        raise ConversionError(f"Could not read {source}: {exc}") from exc


def convert_rows(
    rows: list[dict[str, str]],
    user_rates: dict[date, Decimal],
    *,
    opening_rate: Decimal | None = None,
) -> tuple[list[dict[str, str]], dict[date, Decimal]]:
    """Translate ledger movements and build cumulative EUR balances."""
    chronological = sorted(
        enumerate(rows, start=2),
        key=lambda indexed_row: (
            parse_timestamp(indexed_row[1].get("Date Time", ""), row_number=indexed_row[0]),
            indexed_row[0],
        ),
    )
    first_row_number, first_row = chronological[0]
    opening_gbp = (
        parse_decimal(first_row.get("Running Balance"), field="Running Balance", row_number=first_row_number, required=True)
        - parse_decimal(first_row.get("Amount"), field="Amount", row_number=first_row_number, required=True)
    )
    if opening_gbp and opening_rate is None:
        raise ConversionError(
            f"The statement has a non-zero opening balance of GBP {money(opening_gbp):.2f}; "
            "ask the user for the opening-balance EUR-per-GBP rate"
        )
    if opening_rate is not None and (not opening_rate.is_finite() or opening_rate <= 0):
        raise ConversionError("Opening EUR-per-GBP rate must be a positive finite number")

    eur_balance = money(opening_gbp * opening_rate) if opening_rate is not None else Decimal("0.00")
    output_by_original_index: dict[int, dict[str, str]] = {}
    used_user_rates: dict[date, Decimal] = {}

    for row_number, source_row in chronological:
        if source_row.get("Currency", "").strip() != "GBP":
            raise ConversionError(
                f"Row {row_number}: expected a GBP ledger row, found {source_row.get('Currency')!r}; mixed/non-GBP statements are unsupported"
            )
        rate, is_user_rate = rate_for_row(source_row, user_rates, row_number=row_number)
        transaction_date = parse_date(source_row.get("Date", ""), row_number=row_number)
        if is_user_rate:
            used_user_rates[transaction_date] = rate

        # Amount is the full GBP ledger movement, so the Wise fee already
        # reflected in it remains included in this converted movement.
        amount_gbp = parse_decimal(source_row.get("Amount"), field="Amount", row_number=row_number, required=True)
        amount_eur = money(amount_gbp * rate)
        eur_balance = money(eur_balance + amount_eur)

        row = source_row.copy()
        row["Amount"] = f"{amount_eur:.2f}"
        row["Currency"] = "EUR"
        row["Running Balance"] = f"{eur_balance:.2f}"

        fee_text = source_row.get("Total fees", "")
        if fee_text.strip():
            fee_gbp = parse_decimal(fee_text, field="Total fees", row_number=row_number)
            row["Total fees"] = f"{money(fee_gbp * rate):.2f}"
        if is_user_rate and "Note" in row:
            row["Note"] = append_rate_note(source_row.get("Note", "") or "", rate, transaction_date)
        output_by_original_index[row_number - 2] = row

    return [output_by_original_index[index] for index in range(len(rows))], used_user_rates


def validate_converted_rows(
    source_rows: list[dict[str, str]],
    output_rows: list[dict[str, str]],
    user_rates: dict[date, Decimal],
    *,
    opening_rate: Decimal | None = None,
) -> None:
    """Independently check rates, converted fields, preserved metadata, and cumulative balances."""
    if len(source_rows) != len(output_rows):
        raise ConversionError("Validation failed: output transaction count differs from source")
    converted_fields = {"Amount", "Currency", "Running Balance", "Total fees", "Note"}

    for row_number, (source_row, output_row) in enumerate(zip(source_rows, output_rows), start=2):
        if source_row.get("TransferWise ID") != output_row.get("TransferWise ID") or source_row.get("Date Time") != output_row.get("Date Time"):
            raise ConversionError(f"Validation failed: row identity/order changed at CSV row {row_number}")
        if output_row.get("Currency") != "EUR":
            raise ConversionError(f"Validation failed: row {row_number} is not denominated in EUR")
        for field, value in source_row.items():
            if field not in converted_fields and output_row.get(field) != value:
                raise ConversionError(f"Validation failed: source field {field!r} changed at CSV row {row_number}")

        transaction_date = parse_date(source_row["Date"], row_number=row_number)
        rate, is_user_rate = rate_for_row(source_row, user_rates, row_number=row_number)
        amount_gbp = parse_decimal(source_row.get("Amount"), field="Amount", row_number=row_number, required=True)
        expected_amount = money(amount_gbp * rate)
        if parse_decimal(output_row.get("Amount"), field="EUR Amount", row_number=row_number, required=True) != expected_amount:
            raise ConversionError(f"Validation failed: converted Amount does not match the formula at CSV row {row_number}")

        source_fee_text = source_row.get("Total fees", "")
        if source_fee_text.strip():
            fee_gbp = parse_decimal(source_fee_text, field="Total fees", row_number=row_number)
            expected_fee = money(fee_gbp * rate)
            if parse_decimal(output_row.get("Total fees"), field="EUR Total fees", row_number=row_number, required=True) != expected_fee:
                raise ConversionError(f"Validation failed: converted Total fees does not match the formula at CSV row {row_number}")
        elif output_row.get("Total fees", "") != source_fee_text:
            raise ConversionError(f"Validation failed: blank Total fees field changed at CSV row {row_number}")

        expected_note = (
            append_rate_note(source_row.get("Note", "") or "", rate, transaction_date)
            if is_user_rate and "Note" in source_row
            else source_row.get("Note", "")
        )
        if output_row.get("Note", "") != expected_note:
            raise ConversionError(f"Validation failed: rate provenance note changed at CSV row {row_number}")

    chronological = sorted(
        enumerate(source_rows, start=2),
        key=lambda indexed_row: (
            parse_timestamp(indexed_row[1].get("Date Time", ""), row_number=indexed_row[0]),
            indexed_row[0],
        ),
    )
    first_row_number, first_row = chronological[0]
    opening_gbp = (
        parse_decimal(first_row.get("Running Balance"), field="Running Balance", row_number=first_row_number, required=True)
        - parse_decimal(first_row.get("Amount"), field="Amount", row_number=first_row_number, required=True)
    )
    if opening_gbp and opening_rate is None:
        raise ConversionError("Validation failed: non-zero opening GBP balance needs an opening-rate assumption")
    expected_balance = money(opening_gbp * opening_rate) if opening_rate is not None else Decimal("0.00")
    for row_number, source_row in chronological:
        output_row = output_rows[row_number - 2]
        expected_balance = money(
            expected_balance
            + money(
                parse_decimal(source_row.get("Amount"), field="Amount", row_number=row_number, required=True)
                * rate_for_row(source_row, user_rates, row_number=row_number)[0]
            )
        )
        if parse_decimal(output_row.get("Running Balance"), field="EUR Running Balance", row_number=row_number, required=True) != expected_balance:
            raise ConversionError(f"Validation failed: cumulative Running Balance is incorrect at CSV row {row_number}")


def write_atomically(target: Path, fieldnames: list[str], rows: list[dict[str, str]], *, force: bool) -> None:
    if target.exists() and not force:
        raise ConversionError(f"Output already exists: {target} (use --force only if replacement is authorized)")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary_name = handle.name
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_name, target)
    except Exception:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a non-official EUR translation of a Wise GBP statement using Wise quotes and user-provided daily rates."
    )
    parser.add_argument("input_csv", type=Path, help="Wise GBP statement CSV")
    parser.add_argument("--output", type=Path, help="destination CSV (default: sibling with GBP replaced by EUR)")
    parser.add_argument(
        "--rate",
        action="append",
        default=[],
        metavar="DD-MM-YYYY=EUR_PER_GBP",
        help="user-provided Wise daily rate for rows without a GBP→EUR quote; repeat for each date",
    )
    parser.add_argument(
        "--opening-rate",
        help="EUR per GBP rate for a non-zero opening GBP balance; ask the user if one is needed",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing output (only with user authorization)")
    args = parser.parse_args(argv)

    source = args.input_csv.expanduser().resolve()
    target = (args.output.expanduser().resolve() if args.output else output_path_for(source).resolve())
    if not source.is_file():
        print(f"Error: input file does not exist: {source}", file=sys.stderr)
        return 2
    if source == target:
        print("Error: output path must not be the source file", file=sys.stderr)
        return 2
    if target.exists() and not args.force:
        print(f"Error: output already exists: {target} (use --force only if replacement is authorized)", file=sys.stderr)
        return 2

    try:
        fieldnames, source_rows = read_statement(source)
        opening_gbp = validate_source_running_balances(source_rows)

        user_rates: dict[date, Decimal] = {}
        for rate_arg in args.rate:
            rate_day, rate = parse_rate_override(rate_arg)
            if rate_day in user_rates and user_rates[rate_day] != rate:
                raise ConversionError(
                    f"Conflicting user-provided rates for {rate_day.strftime('%d-%m-%Y')}"
                )
            user_rates[rate_day] = rate

        needed_dates = set(missing_rate_dates(source_rows))
        missing_dates = sorted(needed_dates - set(user_rates))
        if missing_dates:
            date_list = ", ".join(day.strftime("%d-%m-%Y") for day in missing_dates)
            raise ConversionError(
                "Ask the user for a Wise GBP→EUR rate in EUR per GBP for each missing date before converting: "
                + date_list
            )
        extra_dates = sorted(set(user_rates) - needed_dates)
        if extra_dates:
            date_list = ", ".join(day.strftime("%d-%m-%Y") for day in extra_dates)
            raise ConversionError(f"Rates were supplied for dates that need no user rate: {date_list}")

        opening_rate = None
        if args.opening_rate is not None:
            opening_rate = parse_decimal(args.opening_rate, field="opening-rate", row_number=0, required=True)
            if opening_rate <= 0:
                raise ConversionError("--opening-rate must be a positive EUR-per-GBP value")
        if opening_gbp and opening_rate is None:
            raise ConversionError(
                f"The GBP opening balance is {money(opening_gbp):.2f}; ask the user for an opening-balance EUR-per-GBP rate"
            )

        output_rows, used_user_rates = convert_rows(source_rows, user_rates, opening_rate=opening_rate)
        validate_converted_rows(source_rows, output_rows, user_rates, opening_rate=opening_rate)

        write_atomically(target, fieldnames, output_rows, force=args.force)
    except (ConversionError, OSError, csv.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    output_chronological = sorted(
        enumerate(output_rows, start=2),
        key=lambda indexed_row: (
            parse_timestamp(indexed_row[1].get("Date Time", ""), row_number=indexed_row[0]),
            indexed_row[0],
        ),
    )
    output_closing = output_chronological[-1][1]["Running Balance"]
    print(f"Created {target} ({len(output_rows)} transactions).")
    wise_quote_count = sum(
        wise_gbp_eur_rate(row, row_number=index) is not None
        for index, row in enumerate(source_rows, start=2)
    )
    print(f"Wise GBP→EUR quote rows used: {wise_quote_count}.")
    if used_user_rates:
        print("User-provided Wise daily rates used (EUR per GBP):")
        for day, rate in sorted(used_user_rates.items()):
            print(f"  {day.strftime('%d-%m-%Y')}: {rate}")
    print(f"Cumulative EUR closing balance: {output_closing} (no year-end FX adjustment added).")
    print("This is an analytical translation, not an official Wise statement or certified accounting record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
