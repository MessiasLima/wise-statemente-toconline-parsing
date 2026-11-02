---
name: wise-statement-eur-converter
description: Convert Wise GBP account-statement CSVs to EUR. Use this whenever a user asks to convert a Wise statement, transaction export, balance, fees, or movements from GBP to EUR. Use Wise GBP→EUR quotes already present; when a row has no such quote, ask the user for Wise's EUR-per-GBP rate for that date. Never fetch ECB or other substitute rates, guess rates, or silently add a period-end FX adjustment. Preserve the original, calculate cumulative EUR running balances, and explain that the result is not an official or certified accounting statement.
compatibility: Requires Python 3.10+ standard library. No network access is needed.
---

# Wise GBP Statement to EUR

Create a separate EUR translation of a Wise GBP CSV using only explicit Wise GBP→EUR quotes and daily rates supplied by the user for rows without a quote. Financial files are sensitive: use exact decimal arithmetic, never guess an exchange rate, and never represent the translation as an official Wise statement or certified accounting record.

## Scope and safety

- This workflow supports a Wise statement whose ledger currency is GBP and whose target is EUR. Stop and ask before using it for mixed/non-GBP ledgers or other currency pairs.
- Keep the GBP source unchanged. Write a sibling output with `_GBP_` changed to `_EUR_` in the filename, or a clearly named `_EUR` copy if that pattern is absent. Do not replace an existing output unless the user explicitly authorizes replacement.
- If the user mentions tax, audit, regulatory, or statutory use, do not guarantee acceptance or tax treatment. Explain that the file is an analytical translation and recommend confirming the rate/accounting policy and any FX adjustment with their accountant.
- If the source is malformed, GBP balances do not reconcile, dates are ambiguous, or any required daily rate is missing, stop before writing the output.

## Rate collection

1. Use a row's `Exchange Rate` only when `Exchange From=GBP` and `Exchange To=EUR`. This quote is **EUR per GBP**.
2. Find every distinct transaction date with at least one row that lacks an explicit GBP→EUR quote. Present the dates to the user and ask for Wise's EUR-per-GBP rate for each, for example `2026-09-24: 1 GBP = 1.163 EUR`. Wait for all required rates before converting or replacing any file. A user-provided daily rate applies only to rows on that date that lack their own GBP→EUR quote.
3. A `GBP→USD` (or other non-EUR) rate is not a GBP→EUR rate. Preserve that metadata, but use the user's supplied GBP→EUR daily rate for that row.
4. Never query the ECB, another public FX source, or a website as a fallback. Do not interpolate, carry a rate from a different date, or infer a rate from `Exchange To Amount`.
5. If the statement's opening GBP balance is non-zero, ask for an opening-balance EUR-per-GBP rate too. Do not value the opening balance using an arbitrary transaction's rate.

## Conversion and ledger rules

- Use `Decimal`, never binary floating point. Round each converted monetary transaction to two decimals with `ROUND_HALF_UP`.
- `Amount`: multiply the source GBP ledger `Amount` by its applicable EUR-per-GBP rate, retaining its sign. Do this even on GBP→EUR conversion rows: the source `Amount` is the full movement in the GBP ledger and can include a fee. Keep `Exchange To Amount` unchanged as metadata about the actual EUR proceeds; it is not a substitute for the translated full GBP ledger movement.
- `Total fees`: multiply the GBP fee by the same applicable rate. This field is descriptive; do not add the fee to `Amount` a second time because the source ledger `Amount` already represents the posted GBP movement.
- `Currency`: set to `EUR` on translated ledger rows.
- `Running Balance`: make it **cumulative**. Sort a working copy chronologically by `Date Time`, convert the opening GBP balance using the explicitly supplied opening rate if non-zero, then add each rounded EUR `Amount` to the preceding EUR balance. Write results back in the source row order. Do not recalculate balances by directly revaluing each historical GBP balance at that row's rate.
- Do not add a synthetic year-end or period-end FX adjustment. The cumulative EUR ledger may differ from the GBP closing balance valued at a closing-date rate; explain the difference and leave any closing revaluation to the user's accountant unless the user explicitly requests otherwise.
- Preserve row count, row order, transaction IDs, dates, descriptions, payment references, and all source metadata, including non-EUR exchange details and `Exchange To Amount`. Do not overwrite a GBP→USD `Exchange Rate` with the user-provided EUR rate. When the source has a `Note` column, annotate user-provided rates there, appending rather than deleting any existing note; otherwise report the applied rate map in the response without changing the column layout.
- Preserve blank optional fields and keep zero fees as `0.00` where present.

## Procedure

1. Read the actual CSV. Check its headers, row count, order, dates, and that every ledger `Currency` is GBP. Do not rely on a pasted preview when the file is available.
2. Validate source running-balance arithmetic in chronological timestamp order, deriving the opening balance from the oldest row. Stop if any row is out by more than one penny or timestamps leave the order ambiguous.
3. Identify explicit row-level GBP→EUR Wise quotes and list all unique dates needing user rates. Ask for rates in EUR per GBP and wait for the user's answer. Do not create or replace the EUR output while rates are missing.
4. If the opening balance is non-zero, ask for its conversion rate. Confirm that the user-provided rates reflect the rate policy they want; for tax/accounting use, recommend confirming it with their accountant.
5. Review the converter's `--help`, then convert with `scripts/convert_wise_statement.py`, passing one `--rate DD-MM-YYYY=EUR_PER_GBP` argument for each missing-rate date and `--opening-rate EUR_PER_GBP` if needed. The script requires complete rate coverage and does no network requests.
6. Validate before reporting success:
   - original source remains unchanged;
   - output preserves source row count, order, IDs, columns, and metadata (apart from converted fields and appended rate notes);
   - every ledger currency is EUR;
   - each `Amount` and nonblank `Total fees` equals the source GBP value multiplied by the applicable rate and rounded to cents;
   - `GBP→USD` quotes were not used as EUR rates;
   - every EUR `Running Balance` equals the prior cumulative balance plus the converted `Amount`, starting from the stated opening balance;
   - no synthetic end-of-period adjustment was added.
7. Report output path, row count, Wise/user rate coverage, opening and cumulative closing EUR balances, and any difference from a closing-date valuation. State that the file is an analytical translation, not an official Wise statement or tax/accounting certification.

## Bundled converter

Use repeated `--rate` options for the dates collected from the user:

```bash
python3 scripts/convert_wise_statement.py statement_GBP.csv \
  --rate 19-12-2025=1.141 \
  --rate 07-01-2026=1.155
```

For a non-zero opening balance, also provide the rate the user gives for that balance:

```bash
python3 scripts/convert_wise_statement.py statement_GBP.csv \
  --rate 19-12-2025=1.141 \
  --opening-rate 1.140
```

The default output is a sibling EUR copy. Use `--output` for another destination; use `--force` only after the user explicitly authorizes replacing an existing file.
