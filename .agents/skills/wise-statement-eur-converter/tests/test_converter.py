import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import convert_wise_statement as converter  # noqa: E402


FIXTURE = SKILL_ROOT / "evals" / "files" / "synthetic_wise_gbp_statement.csv"


class ConverterTests(unittest.TestCase):
    def setUp(self):
        _, self.rows = converter.read_statement(FIXTURE)
        self.rates = {
            date(2026, 6, 9): Decimal("1.16"),
            date(2026, 9, 24): Decimal("1.17"),
            date(2026, 9, 26): Decimal("1.16"),
        }
        self.opening_rate = Decimal("1.16")

    def convert(self, rates=None):
        return converter.convert_rows(
            self.rows,
            self.rates if rates is None else rates,
            opening_rate=self.opening_rate,
        )

    def test_source_running_balance_reconciles_and_derives_nonzero_opening(self):
        opening = converter.validate_source_running_balances(self.rows)
        self.assertEqual(opening, Decimal("1000.00"))

    def test_conversion_row_translates_full_gbp_ledger_amount_and_builds_cumulative_balance(self):
        output, used_rates = self.convert()
        conversion = output[0]  # newest-first source order
        self.assertEqual(conversion["Amount"], "-115.00")
        self.assertEqual(conversion["Total fees"], "1.15")
        self.assertEqual(conversion["Exchange To Amount"], "115.00")
        self.assertEqual(conversion["Running Balance"], "1154.68")
        self.assertEqual(used_rates, {
            date(2026, 6, 9): Decimal("1.16"),
            date(2026, 9, 24): Decimal("1.17"),
            date(2026, 9, 26): Decimal("1.16"),
        })

    def test_daily_user_rates_apply_to_missing_rows_and_are_recorded(self):
        output, _ = self.convert()
        cashback = output[1]
        transfer = output[2]
        self.assertEqual(cashback["Amount"], "2.32")
        self.assertIn("1 GBP = 1.16 EUR", cashback["Note"])
        self.assertIn("user-provided Wise daily rate", transfer["Note"])
        self.assertEqual(transfer["Amount"], "117.00")

    def test_gbp_to_usd_quote_is_not_used_as_a_gbp_to_eur_rate(self):
        output, _ = self.convert()
        card = output[3]
        self.assertEqual(card["Amount"], "-9.64")
        self.assertEqual(card["Exchange Rate"], "1.33695")
        self.assertEqual(card["Exchange To"], "USD")
        self.assertEqual(card["Exchange To Amount"], "11.08")
        self.assertIn("1 GBP = 1.16 EUR", card["Note"])

    def test_missing_user_rate_stops_conversion_with_date(self):
        incomplete = {
            date(2026, 6, 9): Decimal("1.16"),
            date(2026, 9, 26): Decimal("1.16"),
        }
        with self.assertRaisesRegex(converter.ConversionError, "24-09-2026"):
            converter.convert_rows(self.rows, incomplete, opening_rate=self.opening_rate)

    def test_nonzero_opening_balance_requires_an_explicit_rate(self):
        with self.assertRaisesRegex(converter.ConversionError, "opening-balance EUR-per-GBP rate"):
            converter.convert_rows(self.rows, self.rates)

    def test_inconsistent_source_balance_stops_conversion(self):
        altered = [row.copy() for row in self.rows]
        altered[0]["Running Balance"] = "993.71"
        with self.assertRaisesRegex(converter.ConversionError, "Source balance check failed"):
            converter.validate_source_running_balances(altered)

    def test_output_validation_checks_formulas_metadata_and_cumulative_balances(self):
        output, _ = self.convert()
        converter.validate_converted_rows(self.rows, output, self.rates, opening_rate=self.opening_rate)
        corrupted = [row.copy() for row in output]
        corrupted[0]["Running Balance"] = "1154.69"
        with self.assertRaisesRegex(converter.ConversionError, "cumulative Running Balance"):
            converter.validate_converted_rows(self.rows, corrupted, self.rates, opening_rate=self.opening_rate)

    def test_no_automatic_end_of_period_revaluation_is_added(self):
        output, _ = self.convert()
        self.assertEqual(len(output), len(self.rows))
        cumulative_close = Decimal(output[0]["Running Balance"])
        spot_valuation = converter.money(Decimal("993.69") * Decimal("1.15"))
        self.assertEqual(cumulative_close, Decimal("1154.68"))
        self.assertNotEqual(cumulative_close, spot_valuation)


if __name__ == "__main__":
    unittest.main()
