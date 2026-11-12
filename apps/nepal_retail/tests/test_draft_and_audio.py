import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nepal_retail.voice.audio import encode_pcm16_wav, float32_to_pcm16_wav
from nepal_retail.voice.contracts import InvalidOrder
from nepal_retail.voice.providers import validate_audio


class AudioNormalizerTests(unittest.TestCase):
	def test_float32_and_pcm16_encoding_produce_valid_wav(self):
		samples = [0.0, 0.5, -0.5, 1.2, -1.2] * 1600
		wav_bytes = float32_to_pcm16_wav(samples, sample_rate=8000, channels=1)
		validate_audio(wav_bytes)
		self.assertTrue(wav_bytes.startswith(b"RIFF"))

	def test_invalid_audio_inputs_are_rejected(self):
		with self.assertRaises(InvalidOrder):
			float32_to_pcm16_wav([float("nan")] * 8000, sample_rate=8000)
		with self.assertRaises(InvalidOrder):
			encode_pcm16_wav(b"\x00\x01\x02", sample_rate=8000, channels=1)
		with self.assertRaises(InvalidOrder):
			encode_pcm16_wav(b"\x00\x00" * 8000, sample_rate=4000, channels=1)


class ReviewedDraftTests(unittest.TestCase):
	def setUp(self):
		self.frappe = Mock()
		self.frappe.PermissionError = PermissionError
		self.frappe.ValidationError = ValueError
		self.frappe.throw.side_effect = lambda message, exception=ValueError: self._raise(
			message, exception
		)
		self.profile = SimpleNamespace(
			name="POS-KTM",
			company="Shop",
			warehouse="Main-WH",
			selling_price_list="Standard Selling",
			allow_discount_change=True,
			max_discount=5,
			taxes_and_charges_included=False,
		)
		self.catalogue = SimpleNamespace(profile=self.profile)
		self.items = {
			"MOUSE-1": SimpleNamespace(
				name="MOUSE-1",
				item_name="Wireless Mouse",
				stock_uom="Nos",
				uoms=[SimpleNamespace(uom="Box", conversion_factor=10)],
				disabled=False,
				is_sales_item=True,
				has_variants=False,
				is_fixed_asset=False,
				is_exempt=False,
				is_zero_rated=False,
			),
		}
		self.customers = {
			"CUST-RAM": SimpleNamespace(name="CUST-RAM", disabled=False),
		}
		self.frappe.get_doc.side_effect = self._get_doc
		self.frappe.db.get_value.side_effect = self._get_value

		self.patcher = patch.dict(sys.modules, {"frappe": self.frappe})
		self.patcher.start()
		sys.modules.pop("nepal_retail.voice.draft", None)
		self.draft_mod = importlib.import_module("nepal_retail.voice.draft")
		self.addCleanup(self.patcher.stop)

	@staticmethod
	def _raise(message, exception):
		raise exception(message)

	def _get_doc(self, doctype, name):
		if doctype == "Item":
			return self.items[name]
		if doctype == "Customer":
			return self.customers[name]
		raise KeyError(doctype)

	def _get_value(self, doctype, filters, fieldname):
		if doctype == "Item Price" and filters.get("item_code") == "MOUSE-1":
			if "uom" in filters:
				return None
			return "1000.00"
		return None

	def test_reviewed_draft_uses_server_price_and_never_auto_submits(self):
		draft = self.draft_mod.build_reviewed_cart_draft(
			{
				"items": [{"item_code": "MOUSE-1", "quantity": "2", "uom": "Nos"}],
				"customer": "CUST-RAM",
			},
			self.catalogue,
		)
		self.assertEqual(draft["status"], "draft_ready")
		self.assertFalse(draft["can_submit"])
		self.assertTrue(draft["requires_manual_checkout"])
		self.assertEqual(draft["items"][0]["server_rate"], "1000.00")
		self.assertEqual(draft["taxable_sales"], "2000.00")
		self.assertEqual(draft["vat_amount"], "260.00")
		self.assertEqual(draft["grand_total"], "2260.00")
		self.assertEqual(len(draft["order_revision"]), 64)

	def test_uom_conversion_and_injected_rate_rejection(self):
		draft = self.draft_mod.build_reviewed_cart_draft(
			{"items": [{"item_code": "MOUSE-1", "quantity": "1", "uom": "Box"}]},
			self.catalogue,
		)
		self.assertEqual(draft["items"][0]["server_rate"], "10000.00")
		with self.assertRaises(InvalidOrder):
			self.draft_mod.build_reviewed_cart_draft(
				{"items": [{"item_code": "MOUSE-1", "quantity": "1", "uom": "Kg"}]},
				self.catalogue,
			)
		with self.assertRaises(InvalidOrder):
			self.draft_mod.build_reviewed_cart_draft(
				{"items": [{"item_code": "MOUSE-1", "quantity": "1", "uom": "Nos", "rate": "1"}]},
				self.catalogue,
			)

	def test_discount_exceeding_threshold_requires_manager_approval(self):
		with self.assertRaises(PermissionError):
			self.draft_mod.build_reviewed_cart_draft(
				{
					"items": [{"item_code": "MOUSE-1", "quantity": "1", "uom": "Nos"}],
					"discount": {"amount": "10", "kind": "percent", "scope": "invoice"},
				},
				self.catalogue,
			)
		approved = self.draft_mod.build_reviewed_cart_draft(
			{
				"items": [{"item_code": "MOUSE-1", "quantity": "1", "uom": "Nos"}],
				"discount": {"amount": "10", "kind": "percent", "scope": "invoice"},
				"manager_approval_reference": "MGR-APPROVAL-01",
			},
			self.catalogue,
		)
		self.assertEqual(approved["discount_total"], "100.00")
		self.assertEqual(approved["manager_approval_reference"], "MGR-APPROVAL-01")
