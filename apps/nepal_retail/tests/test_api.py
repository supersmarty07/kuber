import importlib
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class APITests(unittest.TestCase):
	def setUp(self):
		self.frappe = Mock()
		self.frappe.whitelist = lambda **kwargs: lambda function: function
		self.frappe.PermissionError = PermissionError
		self.frappe.ValidationError = ValueError
		self.frappe.session = SimpleNamespace(user="cashier@example.test")
		self.frappe.conf = {"nepal_retail_preview_enabled": True, "nepal_retail_hosted_voice_enabled": True}
		self.frappe.cache.return_value.incr.return_value = 1
		self.frappe.throw.side_effect = lambda message, exception=ValueError: self.raise_error(
			message, exception
		)
		self.modules = patch.dict(sys.modules, {"frappe": self.frappe})
		self.modules.start()
		for name in ("nepal_retail.api.voice", "nepal_retail.voice.catalogue"):
			sys.modules.pop(name, None)
		self.api = importlib.import_module("nepal_retail.api.voice")
		self.catalogue_patch = patch.object(self.api, "POSCatalogue")
		self.catalogue = self.catalogue_patch.start()
		self.catalogue.return_value.search.return_value = []
		self.provider_patch = patch.object(self.api, "provider")
		self.provider = self.provider_patch.start()
		self.addCleanup(self.modules.stop)
		self.addCleanup(self.catalogue_patch.stop)
		self.addCleanup(self.provider_patch.stop)

	@staticmethod
	def raise_error(message, exception):
		raise exception(message)

	def test_guest_and_disabled_flags_block_before_lookup(self):
		for user, preview_enabled, hosted_enabled in (
			("Guest", True, True),
			("cashier", False, True),
			("cashier", True, False),
		):
			with self.subTest(user=user, preview=preview_enabled, hosted=hosted_enabled):
				self.frappe.session.user = user
				self.frappe.conf.update(
					nepal_retail_preview_enabled=preview_enabled,
					nepal_retail_hosted_voice_enabled=hosted_enabled,
				)
				with self.assertRaises(PermissionError):
					self.api.preview_transcript("POS-1", "mouse")
		self.catalogue.assert_not_called()
		self.provider.assert_not_called()

	def test_profile_denial_prevents_provider_call(self):
		self.catalogue.side_effect = PermissionError("denied")
		with self.assertRaises(PermissionError):
			self.api.preview_transcript("other-store", "mouse")
		self.provider.assert_not_called()

	def test_request_limit_prevents_provider_call(self):
		self.frappe.cache.return_value.incr.return_value = 11
		with self.assertRaises(ValueError):
			self.api.preview_transcript("POS-1", "mouse")
		self.provider.assert_not_called()

	def test_rate_limit_keys_are_site_scoped(self):
		with patch.object(self.api.time, "time", return_value=120):
			for site in ("first.test", "second.test"):
				self.frappe.local.site = site
				self.api.authorize("POS-1")
		keys = [call.args[0] for call in self.frappe.cache.return_value.incr.call_args_list]
		self.assertEqual(
			keys,
			[
				"nepal_retail:voice:first.test:cashier@example.test:2",
				"nepal_retail:voice:second.test:cashier@example.test:2",
			],
		)

	def test_transcript_endpoint_returns_review_only(self):
		self.provider.return_value.extract.return_value = {"items": [{"description": "mouse", "quantity": 1}]}
		result = self.api.preview_transcript("POS-1", "euta mouse")
		self.assertEqual(result["status"], "review_required")
		self.assertFalse(result["can_submit"])
		self.frappe.has_permission.assert_called_once_with("POS Invoice", ptype="create", throw=True)
		self.frappe.get_doc.assert_not_called()
		self.frappe.db.commit.assert_not_called()

	def test_uploaded_audio_is_not_saved(self):
		self.frappe.request.files = {"audio": SimpleNamespace(stream=io.BytesIO(b"test audio"))}
		self.provider.return_value.transcribe.return_value = "mouse"
		result = self.api.transcribe("POS-1")
		self.assertEqual(result, {"transcript": "mouse", "requires_review": True})
		self.provider.return_value.transcribe.assert_called_once_with(b"test audio")
		self.frappe.get_doc.assert_not_called()

	def test_untrusted_order_is_rejected(self):
		with self.assertRaises(ValueError):
			self.api.preview("POS-1", {"items": [], "submit": True})
		self.catalogue.return_value.search.assert_not_called()
