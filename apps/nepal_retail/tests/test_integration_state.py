import unittest
from dataclasses import FrozenInstanceError

from nepal_retail.compliance.submission import FiscalSubmission, SubmissionState
from nepal_retail.payments.state import PaymentIntent, PaymentState


class PaymentStateTests(unittest.TestCase):
	def setUp(self):
		self.intent = PaymentIntent.create("intent-1", "Shop", "cart-revision-1", "100.25")

	def test_amounts_are_exact_and_intent_is_frozen(self):
		self.assertEqual(self.intent.amount_minor, 10025)
		with self.assertRaises(FrozenInstanceError):
			self.intent.amount_minor = 1
		for amount in (True, "NaN", 0, "1.001"):
			with self.subTest(amount=amount), self.assertRaises(ValueError):
				PaymentIntent.create("id", "Shop", "revision", amount)

	def test_changed_cart_and_unknown_payment_cannot_start(self):
		with self.assertRaises(ValueError):
			self.intent.start("new revision")
		unknown = self.intent.start("cart-revision-1").mark_unknown()
		with self.assertRaises(ValueError):
			unknown.start("cart-revision-1")
		with self.assertRaises(ValueError):
			unknown.close_unpaid(PaymentState.EXPIRED)

	def test_verified_success_is_idempotent_but_mismatches_fail(self):
		intent = self.intent.start("cart-revision-1")
		for amount, currency in ((10024, "NPR"), (10025, "USD"), (True, "NPR")):
			with self.subTest(amount=amount, currency=currency), self.assertRaises(ValueError):
				intent.record_verified_success(amount, currency, "reference")
		paid = intent.record_verified_success(10025, "NPR", "reference")
		self.assertEqual(paid.state, PaymentState.PAID)
		self.assertIs(paid.record_verified_success(10025, "NPR", "reference"), paid)
		with self.assertRaises(ValueError):
			paid.record_verified_success(10025, "NPR", "different-reference")
		with self.assertRaises(ValueError):
			paid.close_unpaid(PaymentState.FAILED)


class FiscalSubmissionTests(unittest.TestCase):
	def test_payload_snapshot_is_independent_and_canonical(self):
		payload = {"test_field": ["original"]}
		submission = FiscalSubmission.create("Shop", "Sales Invoice", "INV-1", payload)
		payload["test_field"].append("changed")
		self.assertNotIn("changed", submission.payload_json)
		self.assertEqual(
			submission.payload_hash,
			FiscalSubmission.create(
				"Shop", "Sales Invoice", "INV-1", {"test_field": ["original"]}
			).payload_hash,
		)
		with self.assertRaises(ValueError):
			FiscalSubmission.create("Shop", "Sales Invoice", "INV-1", {"amount": float("nan")})

	def test_unknown_submission_requires_explicit_safe_retry(self):
		unknown = FiscalSubmission.create(
			"Shop", "Sales Invoice", "INV-1", {"test_field": "fixture"}
		).mark_unknown()
		with self.assertRaises(ValueError):
			unknown.prepare_retry()
		self.assertEqual(
			unknown.prepare_retry(provider_retry_confirmed_safe=True).state, SubmissionState.PENDING
		)
		accepted = unknown.acknowledge("ack-1")
		self.assertIs(accepted.acknowledge("ack-1"), accepted)
		with self.assertRaises(ValueError):
			accepted.acknowledge("different-ack")
