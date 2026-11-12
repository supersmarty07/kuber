import unittest

from nepal_retail.payments.qr import (
	build_dynamic_qr,
	sign_qr_callback,
	verify_and_record_qr_callback,
)
from nepal_retail.payments.repository import PaymentRepository
from nepal_retail.payments.state import PaymentState
from nepal_retail.payments.terminal import CommandState, TerminalAgentCoordinator


class PaymentRepositoryAndQrTests(unittest.TestCase):
	def setUp(self):
		self.repo = PaymentRepository()
		self.secret = "merchant-hmac-secret-key-2026"

	def test_duplicate_active_order_revision_is_blocked(self):
		self.repo.create_intent("intent-1", "Shop", "rev-1", "250.00")
		self.repo.start_payment("intent-1", "rev-1")
		with self.assertRaises(ValueError):
			self.repo.create_intent("intent-2", "Shop", "rev-1", "250.00")

	def test_verified_qr_callback_and_idempotent_deduplication(self):
		self.repo.create_intent("intent-qr", "Shop", "rev-qr", "500.50")
		started = self.repo.start_payment("intent-qr", "rev-qr")
		qr = build_dynamic_qr("fonepay", "MERCHANT-1", self.secret, started.intent, now=1000)
		self.assertEqual(qr.amount_minor, 50050)

		sig = sign_qr_callback(
			self.secret,
			provider="fonepay",
			event_id="evt-100",
			merchant_id="MERCHANT-1",
			intent_id="intent-qr",
			order_revision="rev-qr",
			amount_minor=50050,
			currency="NPR",
			provider_reference="FP-REF-99",
			timestamp=1050,
		)
		callback = {
			"event_id": "evt-100",
			"merchant_id": "MERCHANT-1",
			"intent_id": "intent-qr",
			"order_revision": "rev-qr",
			"amount_minor": 50050,
			"currency": "NPR",
			"provider_reference": "FP-REF-99",
			"timestamp": 1050,
			"signature": sig,
		}
		record = verify_and_record_qr_callback(
			self.repo,
			provider="fonepay",
			merchant_id="MERCHANT-1",
			secret_key=self.secret,
			callback_payload=callback,
			now=1060,
		)
		self.assertEqual(record.intent.state, PaymentState.PAID)
		duplicate = verify_and_record_qr_callback(
			self.repo,
			provider="fonepay",
			merchant_id="MERCHANT-1",
			secret_key=self.secret,
			callback_payload=callback,
			now=1065,
		)
		self.assertEqual(duplicate, record)

		with self.assertRaises(ValueError):
			verify_and_record_qr_callback(
				self.repo,
				provider="fonepay",
				merchant_id="MERCHANT-1",
				secret_key=self.secret,
				callback_payload={**callback, "signature": "0" * 64},
				now=1065,
			)

	def test_paid_but_unissued_invoice_recovery_and_refund_bounds(self):
		self.repo.create_intent("intent-inv", "Shop", "rev-inv", "100.00")
		self.repo.start_payment("intent-inv", "rev-inv")
		self.repo.apply_verified_event(
			provider="esewa",
			event_id="evt-1",
			intent_id="intent-inv",
			amount_minor=10000,
			currency="NPR",
			provider_reference="ES-1",
		)
		failed = self.repo.record_invoice_failure("intent-inv", "Deadlock during POS Invoice insert")
		self.assertTrue(failed.invoice_issue_failed)
		with self.assertRaises(ValueError):
			self.repo.create_intent("intent-recharge", "Shop", "rev-inv", "100.00")

		attached = self.repo.attach_issued_invoice("intent-inv", "POS-INV-0001")
		self.assertFalse(attached.invoice_issue_failed)
		self.assertEqual(attached.invoice_name, "POS-INV-0001")

		settled = self.repo.reconcile_settlement(
			"intent-inv",
			settled_amount_minor=10000,
			currency="NPR",
			provider_reference="ES-1",
			settlement_reference="SETTLE-2083-1",
		)
		self.assertEqual(settled.settlement_reference, "SETTLE-2083-1")

		refunded = self.repo.record_authorized_refund(
			"intent-inv",
			refund_amount_minor=4000,
			refund_reference="REF-1",
			manager_approval="MGR-1",
		)
		self.assertEqual(refunded.refunded_amount_minor, 4000)
		with self.assertRaises(ValueError):
			self.repo.record_authorized_refund(
				"intent-inv",
				refund_amount_minor=7000,
				refund_reference="REF-2",
				manager_approval="MGR-1",
			)


class TerminalAgentTests(unittest.TestCase):
	def setUp(self):
		self.repo = PaymentRepository()
		self.coordinator = TerminalAgentCoordinator()
		self.secret = "terminal-device-shared-secret-01"
		self.coordinator.register_device("TERM-1", "Shop", "POS-1", self.secret)

	def test_outbound_poll_ttl_and_cardholder_data_rejection(self):
		self.repo.create_intent("intent-t1", "Shop", "rev-t1", "300.00")
		started = self.repo.start_payment("intent-t1", "rev-t1")
		self.coordinator.enqueue_command(
			"cmd-expired",
			"TERM-1",
			started.intent,
			now=100,
			ttl_seconds=60,
		)
		self.assertIsNone(
			self.coordinator.poll_next_command("TERM-1", self.secret, now=200)
		)

		self.coordinator.enqueue_command(
			"cmd-active",
			"TERM-1",
			started.intent,
			now=200,
			ttl_seconds=120,
		)
		polled = self.coordinator.poll_next_command("TERM-1", self.secret, now=210)
		self.assertEqual(polled.state, CommandState.DISPATCHED)

		for forbidden_key in ("pan", "pin", "cvv", "track2"):
			with self.subTest(forbidden_key=forbidden_key), self.assertRaises(ValueError):
				self.coordinator.report_terminal_result(
					self.repo,
					device_id="TERM-1",
					shared_secret=self.secret,
					command_id="cmd-active",
					event_id="term-evt-1",
					status="approved",
					amount_minor=30000,
					currency="NPR",
					provider_reference="ECR-99",
					metadata={forbidden_key: "secret-card-data"},
				)

		paid = self.coordinator.report_terminal_result(
			self.repo,
			device_id="TERM-1",
			shared_secret=self.secret,
			command_id="cmd-active",
			event_id="term-evt-1",
			status="approved",
			amount_minor=30000,
			currency="NPR",
			provider_reference="ECR-99",
			metadata={"aid": "A0000000031010"},
		)
		self.assertEqual(paid.intent.state, PaymentState.PAID)

		self.coordinator.revoke_device("TERM-1")
		with self.assertRaises(PermissionError):
			self.coordinator.poll_next_command("TERM-1", self.secret, now=220)
