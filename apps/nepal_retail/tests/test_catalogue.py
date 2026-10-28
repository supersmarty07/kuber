import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class CatalogueTests(unittest.TestCase):
	def setUp(self):
		self.frappe = Mock()
		self.frappe.PermissionError = PermissionError
		self.frappe.session.user = "cashier"
		self.frappe.throw.side_effect = lambda message, exception=ValueError: self.raise_error(
			message, exception
		)
		self.profile = Mock(
			name="POS-1", disabled=False, company="Shop", warehouse="Main", selling_price_list="Retail"
		)
		self.profile.name = "POS-1"
		self.profile.get.return_value = [SimpleNamespace(user="cashier")]
		self.frappe.get_doc.return_value = self.profile
		self.frappe.db.get_value.return_value = "Shop"
		self.native_pos = Mock()
		with patch.dict(sys.modules, {"frappe": self.frappe}):
			sys.modules.pop("nepal_retail.voice.catalogue", None)
			self.module = importlib.import_module("nepal_retail.voice.catalogue")
		self.native_patch = patch.dict(
			sys.modules, {"erpnext.selling.page.point_of_sale.point_of_sale": self.native_pos}
		)
		self.native_patch.start()
		self.addCleanup(self.native_patch.stop)

	@staticmethod
	def raise_error(message, exception):
		raise exception(message)

	def test_disabled_and_unassigned_profiles_are_denied(self):
		self.profile.disabled = True
		with self.assertRaises(PermissionError):
			self.module.POSCatalogue("POS-1")
		self.profile.disabled = False
		self.profile.get.return_value = [SimpleNamespace(user="someone-else")]
		with self.assertRaises(PermissionError):
			self.module.POSCatalogue("POS-1")

	def test_warehouse_must_belong_to_profile_company(self):
		self.frappe.db.get_value.return_value = "Other Shop"
		with self.assertRaises(ValueError):
			self.module.POSCatalogue("POS-1")

	def test_permissions_and_item_flags_filter_native_results(self):
		catalogue = self.module.POSCatalogue("POS-1")
		names = ("good", "denied", "disabled", "variant", "asset", "not-for-sale")
		self.native_pos.get_items.return_value = {"items": [{"item_code": name} for name in names]}
		self.frappe.has_permission.side_effect = lambda doctype, doc=None, **kwargs: doc != "denied"
		items = {
			name: SimpleNamespace(
				name=name,
				item_name=name,
				stock_uom="Nos",
				disabled=name == "disabled",
				has_variants=name == "variant",
				is_fixed_asset=name == "asset",
				is_sales_item=name != "not-for-sale",
			)
			for name in names
		}
		self.frappe.get_doc.side_effect = lambda doctype, name: items[name]
		self.assertEqual(
			catalogue.search("mouse"), [{"item_code": "good", "item_name": "good", "stock_uom": "Nos"}]
		)
		self.native_pos.get_items.assert_called_once_with(
			start=0,
			page_length=6,
			price_list="Retail",
			item_group="",
			pos_profile="POS-1",
			search_term="mouse",
		)

	def test_profile_checks_include_company_and_warehouse(self):
		self.module.POSCatalogue("POS-1")
		calls = self.frappe.has_permission.call_args_list
		self.assertEqual(
			[(call.args[0], call.kwargs["doc"]) for call in calls],
			[("POS Profile", "POS-1"), ("Company", "Shop"), ("Warehouse", "Main")],
		)
