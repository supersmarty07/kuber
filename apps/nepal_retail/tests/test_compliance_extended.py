import json
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from nepal_retail.compliance.cbms import CBMSClient, CBMSOutbox, build_cbms_payload
from nepal_retail.compliance.numbering import FiscalSequenceManager
from nepal_retail.compliance.protection import (
	LegalInvoiceMode,
	block_fiscal_cancel_delete_rename,
	enforce_fiscal_immutability,
	should_emit_cbms_for_invoice,
	validate_credit_note,
)
from nepal_retail.compliance.submission import FiscalSubmission, SubmissionState
from nepal_retail.localisation.vat import TaxCategory, TaxLineInput, calculate_invoice_taxes


class FiscalNumberingAndProtectionTests(unittest.TestCase):
	def test_sequence_rollover_across_bs_fiscal_year_and_return_series(self):
		sequences = FiscalSequenceManager()
		before_shrawan = date(2026, 7, 16)
		on_shrawan = date(2026, 7, 17)

		first = sequences.next_number("Shop", "ktm01", before_shrawan, invoice_name="SINV-1")
		repeat = sequences.next_number("Shop", "ktm01", before_shrawan, invoice_name="SINV-1")
		second = sequences.next_number("Shop", "ktm01", before_shrawan, invoice_name="SINV-2")
		new_fy = sequences.next_number("Shop", "ktm01", on_shrawan, invoice_name="SINV-3")
		credit_note = sequences.next_number(
			"Shop",
			"ktm01",
			on_shrawan,
			invoice_name="SINV-RET-1",
			is_return=True,
		)

		self.assertEqual(first, repeat)
		self.assertEqual(first.fiscal_number, "TI-KTM01-2082/83-000001")
		self.assertEqual(second.fiscal_number, "TI-KTM01-2082/83-000002")
		self.assertEqual(new_fy.fiscal_number, "TI-KTM01-2083/84-000001")
		self.assertEqual(credit_note.fiscal_number, "CN-KTM01-2083/84-000001")

	def test_single_legal_invoice_rule_prevents_consolidation_duplicates(self):
		pos_inv = SimpleNamespace(doctype="POS Invoice", docstatus=1, is_consolidated=0)
		consolidated_sinv = SimpleNamespace(doctype="Sales Invoice", docstatus=1, is_consolidated=1)
		direct_sinv = SimpleNamespace(doctype="Sales Invoice", docstatus=1, is_consolidated=0)

		self.assertTrue(
			should_emit_cbms_for_invoice(pos_inv, configured_mode=LegalInvoiceMode.POS_INVOICE)
		)
		self.assertFalse(
			should_emit_cbms_for_invoice(
				consolidated_sinv,
				configured_mode=LegalInvoiceMode.POS_INVOICE,
			)
		)
		self.assertTrue(
			should_emit_cbms_for_invoice(direct_sinv, configured_mode=LegalInvoiceMode.POS_INVOICE)
		)
		self.assertFalse(
			should_emit_cbms_for_invoice(pos_inv, configured_mode=LegalInvoiceMode.SALES_INVOICE)
		)
		self.assertTrue(
			should_emit_cbms_for_invoice(
				consolidated_sinv,
				configured_mode=LegalInvoiceMode.SALES_INVOICE,
			)
		)

	def test_immutability_and_credit_note_validation(self):
		original = SimpleNamespace(
			name="SINV-100",
			docstatus=1,
			company="Shop",
			customer="CUST-1",
			tax_id="600123456",
			posting_date="2026-11-12",
			is_return=0,
			return_against=None,
			net_total=Decimal("1000.00"),
			total_taxes_and_charges=Decimal("130.00"),
			discount_amount=Decimal("0.00"),
			grand_total=Decimal("1130.00"),
			cbms_fiscal_number="TI-KTM-2083/84-000001",
			cbms_fiscal_year="2083/84",
			status="Unpaid",
			outstanding_amount=Decimal("1130.00"),
			items=[SimpleNamespace(item_code="MOUSE", qty=1, rate=1000)],
		)
		status_updated = SimpleNamespace(**{**original.__dict__, "status": "Paid", "outstanding_amount": Decimal("0.00")})
		enforce_fiscal_immutability(status_updated, original)

		tampered_total = SimpleNamespace(**{**original.__dict__, "grand_total": Decimal("900.00")})
		with self.assertRaises(PermissionError):
			enforce_fiscal_immutability(tampered_total, original)

		for action in ("cancel", "trash", "rename"):
			with self.subTest(action=action), self.assertRaises(PermissionError):
				block_fiscal_cancel_delete_rename(original, action)

		valid_cn = SimpleNamespace(
			is_return=1,
			return_against="SINV-100",
			company="Shop",
			customer="CUST-1",
			return_reason="Defective unit returned by customer",
			grand_total=Decimal("-1130.00"),
		)
		validate_credit_note(valid_cn, original)

		excessive_cn = SimpleNamespace(**{**valid_cn.__dict__, "grand_total": Decimal("-2000.00")})
		with self.assertRaises(ValueError):
			validate_credit_note(excessive_cn, original)


class CBMSOutboxAndClientTests(unittest.TestCase):
	def setUp(self):
		self.summary = calculate_invoice_taxes(
			[
				TaxLineInput("MOUSE", Decimal("1"), Decimal("1000"), TaxCategory.TAXABLE),
				TaxLineInput("BOOK", Decimal("1"), Decimal("200"), TaxCategory.EXEMPT),
			]
		)
		self.payload = build_cbms_payload(
			username="ird_user",
			password="ird_password",
			seller_pan="600123456",
			buyer_pan="600987654",
			buyer_name="Everest Traders",
			posting_date=date(2026, 11, 12),
			fiscal_number="TI-KTM-2083/84-000001",
			tax_summary=self.summary,
			datetime_client="2026-11-12T23:30:00",
		)
		self.submission = FiscalSubmission.create("Shop", "Sales Invoice", "SINV-1", self.payload)

	def test_cbms_payload_fields_and_https_enforcement(self):
		self.assertEqual(self.payload["seller_pan"], "600123456")
		self.assertEqual(self.payload["fiscal_year"], "2083.84")
		self.assertEqual(self.payload["total_sales"], 1200.0)
		self.assertEqual(self.payload["taxable_sales_vat"], 1000.0)
		self.assertEqual(self.payload["tax_exempted_sales"], 200.0)
		self.assertEqual(self.payload["vat"], 130.0)
		with self.assertRaises(ValueError):
			CBMSClient("http://cbms.ird.gov.np/api/bill")

	def test_outbox_after_commit_rollback_and_unknown_recovery(self):
		outbox = CBMSOutbox()
		dispatched: list[str] = []
		outbox.enqueue_in_transaction(
			self.submission,
			on_after_commit=lambda: dispatched.append("SINV-1"),
		)
		outbox.rollback_uncommitted("Shop", "Sales Invoice", "SINV-1")
		self.assertEqual(outbox.flush_after_commit_hooks(), 0)
		self.assertEqual(dispatched, [])

		outbox.enqueue_in_transaction(
			self.submission,
			on_after_commit=lambda: dispatched.append("SINV-1"),
		)
		self.assertEqual(outbox.flush_after_commit_hooks(), 1)
		self.assertEqual(dispatched, ["SINV-1"])

		transport = Mock()
		transport.post.return_value = MagicMock()
		response = transport.post.return_value.__enter__.return_value
		response.status_code = 503
		response.iter_content.return_value = [b"service unavailable"]

		client = CBMSClient("https://cbms.ird.gov.np/api/bill", transport=transport)
		unknown = outbox.dispatch("Shop", "Sales Invoice", "SINV-1", client)
		self.assertEqual(unknown.state, SubmissionState.UNKNOWN)
		self.assertEqual(len(outbox.list_pending_reconciliation()), 1)

		outbox.prepare_safe_retry(
			"Shop",
			"Sales Invoice",
			"SINV-1",
			provider_retry_confirmed_safe=True,
		)
		response.status_code = 200
		response.iter_content.return_value = [
			json.dumps({"code": 200, "acknowledgement": "IRD-ACK-9001"}).encode()
		]
		accepted = outbox.dispatch("Shop", "Sales Invoice", "SINV-1", client)
		self.assertEqual(accepted.state, SubmissionState.ACCEPTED)
		self.assertEqual(accepted.acknowledgement, "IRD-ACK-9001")
		self.assertEqual(len(outbox.list_pending_reconciliation()), 0)
