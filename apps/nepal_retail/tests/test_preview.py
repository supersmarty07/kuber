import unittest
from unittest.mock import Mock

from nepal_retail.voice.contracts import InvalidOrder
from nepal_retail.voice.preview import preview_order


class PreviewTests(unittest.TestCase):
	def test_candidates_are_never_automatically_selected(self):
		catalogue = Mock()
		catalogue.search.return_value = [{"item_code": "MOUSE-1", "stock_uom": "Nos"}]
		result = preview_order(
			{"items": [{"description": "wireless mouse", "quantity": 1}], "customer": "Ram"}, catalogue
		)
		self.assertEqual(result["status"], "review_required")
		self.assertFalse(result["can_submit"])
		self.assertTrue(result["items"][0]["requires_selection"])
		self.assertEqual(result["customer_query"], "Ram")
		self.assertNotIn("customer", result)
		catalogue.search.assert_called_once_with("wireless mouse")

	def test_discount_remains_an_unauthorized_request(self):
		catalogue = Mock()
		catalogue.search.return_value = []
		result = preview_order(
			{
				"items": [{"description": "mouse", "quantity": 1}],
				"discount": {"amount": 500, "kind": "amount", "scope": "unspecified"},
			},
			catalogue,
		)
		self.assertTrue(result["discount_request"]["requires_authorization"])
		self.assertEqual(result["items"][0]["candidates"], [])

	def test_entire_order_is_validated_before_catalogue_access(self):
		catalogue = Mock()
		with self.assertRaises(InvalidOrder):
			preview_order(
				{
					"items": [
						{"description": "mouse", "quantity": 1},
						{"description": "keyboard", "quantity": -1},
					]
				},
				catalogue,
			)
		catalogue.search.assert_not_called()

	def test_catalogue_failure_does_not_create_fallback_items(self):
		catalogue = Mock()
		catalogue.search.side_effect = PermissionError("denied")
		with self.assertRaises(PermissionError):
			preview_order({"items": [{"description": "mouse", "quantity": 1}]}, catalogue)
