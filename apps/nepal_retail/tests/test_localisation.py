import importlib
import sys
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nepal_retail.localisation.calendar import (
	BikramSambatDate,
	ad_to_bs,
	bs_to_ad,
	fiscal_year_for_date,
)
from nepal_retail.localisation.vat import (
	TaxCategory,
	TaxLineInput,
	calculate_invoice_taxes,
	validate_pan,
)


class CalendarTests(unittest.TestCase):
	def test_epoch_and_roundtrip_conversions(self):
		epoch_bs = BikramSambatDate(2070, 1, 1)
		self.assertEqual(bs_to_ad(epoch_bs), date(2013, 4, 14))
		self.assertEqual(ad_to_bs(date(2013, 4, 14)), epoch_bs)

		sample_dates = (
			date(2016, 4, 13),
			date(2020, 7, 16),
			date(2025, 4, 14),
			date(2026, 7, 16),
			date(2026, 7, 17),
			date(2026, 11, 12),
		)
		for sample in sample_dates:
			with self.subTest(sample=sample):
				bs_date = ad_to_bs(sample)
				self.assertEqual(bs_to_ad(bs_date), sample)

	def test_shrawan_first_fiscal_year_rollover(self):
		ashadh_end_ad = bs_to_ad(BikramSambatDate(2083, 3, 32))
		shrawan_first_ad = bs_to_ad(BikramSambatDate(2083, 4, 1))
		fy_before = fiscal_year_for_date(ashadh_end_ad)
		fy_after = fiscal_year_for_date(shrawan_first_ad)
		self.assertEqual(fy_before.label, "2082/83")
		self.assertEqual(fy_before.cbms_label, "2082.83")
		self.assertEqual(fy_after.label, "2083/84")
		self.assertEqual(fy_after.cbms_label, "2083.84")
		self.assertEqual(fy_after.start_ad, shrawan_first_ad)

	def test_invalid_bs_dates_are_rejected(self):
		for year, month, day in ((2069, 1, 1), (2083, 0, 1), (2083, 13, 1), (2083, 1, 32), (True, 1, 1)):
			with self.subTest(year=year, month=month, day=day), self.assertRaises(ValueError):
				BikramSambatDate(year, month, day)


class VatAndPanTests(unittest.TestCase):
	def test_pan_validation(self):
		self.assertEqual(validate_pan(" 600123456 "), "600123456")
		for invalid in ("", "12345678", "1234567890", "000000000", "60012345A", "६००१२३४५६", None):
			with self.subTest(invalid=invalid), self.assertRaises(ValueError):
				validate_pan(invalid)

	def test_exclusive_tax_with_mixed_categories_and_discount(self):
		summary = calculate_invoice_taxes(
			[
				TaxLineInput("MOUSE", Decimal("2"), Decimal("1000"), TaxCategory.TAXABLE),
				TaxLineInput("APPLE", Decimal("1"), Decimal("500"), TaxCategory.EXEMPT),
				TaxLineInput("EXPORT-TEA", Decimal("1"), Decimal("500"), TaxCategory.ZERO_RATED),
			],
			discount={"amount": "10", "kind": "percent", "scope": "invoice"},
			prices_include_tax=False,
		)
		self.assertEqual(summary.gross_net_total, Decimal("3000.00"))
		self.assertEqual(summary.discount_total, Decimal("300.00"))
		self.assertEqual(summary.taxable_sales, Decimal("1800.00"))
		self.assertEqual(summary.exempt_sales, Decimal("450.00"))
		self.assertEqual(summary.zero_rated_sales, Decimal("450.00"))
		self.assertEqual(summary.vat_amount, Decimal("234.00"))
		self.assertEqual(summary.grand_total, Decimal("2934.00"))

	def test_tax_inclusive_pricing_extracts_net_and_vat(self):
		summary = calculate_invoice_taxes(
			[TaxLineInput("ITEM-INC", Decimal("1"), Decimal("1130.00"), TaxCategory.TAXABLE)],
			prices_include_tax=True,
		)
		self.assertEqual(summary.taxable_sales, Decimal("1000.00"))
		self.assertEqual(summary.vat_amount, Decimal("130.00"))
		self.assertEqual(summary.grand_total, Decimal("1130.00"))


class LocalisationSetupTests(unittest.TestCase):
	def test_verify_company_localisation_reports_readiness(self):
		frappe_mock = Mock()
		frappe_mock.get_doc.return_value = SimpleNamespace(
			name="Himalayan Retail",
			default_currency="NPR",
			tax_id="600123456",
		)
		frappe_mock.db.get_single_value.return_value = "Asia/Kathmandu"
		frappe_mock.db.exists.return_value = True
		with patch.dict(sys.modules, {"frappe": frappe_mock}):
			sys.modules.pop("nepal_retail.localisation.setup", None)
			setup_mod = importlib.import_module("nepal_retail.localisation.setup")
			report = setup_mod.verify_company_localisation("Himalayan Retail")
			self.assertTrue(report.ready)
			self.assertEqual(report.issues, ())

			frappe_mock.db.exists.return_value = False
			report_missing = setup_mod.verify_company_localisation("Himalayan Retail")
			self.assertFalse(report_missing.ready)
			self.assertEqual(len(report_missing.missing_tax_templates), 3)
