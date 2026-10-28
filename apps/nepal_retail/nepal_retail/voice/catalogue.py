import frappe


class POSCatalogue:
	def __init__(self, pos_profile):
		if not isinstance(pos_profile, str) or not pos_profile.strip() or len(pos_profile) > 140:
			frappe.throw("A valid POS Profile is required")
		frappe.has_permission("POS Profile", doc=pos_profile, throw=True)
		self.profile = frappe.get_doc("POS Profile", pos_profile)
		if self.profile.disabled:
			frappe.throw("This POS Profile is disabled", frappe.PermissionError)
		users = {row.user for row in self.profile.get("applicable_for_users", [])}
		if users and frappe.session.user not in users:
			frappe.throw("This POS Profile is not assigned to you", frappe.PermissionError)
		for doctype, name in (("Company", self.profile.company), ("Warehouse", self.profile.warehouse)):
			if not name:
				frappe.throw(f"POS Profile needs a {doctype}")
			frappe.has_permission(doctype, doc=name, throw=True)
		if frappe.db.get_value("Warehouse", self.profile.warehouse, "company") != self.profile.company:
			frappe.throw("POS warehouse must belong to the profile company")

	def search(self, description):
		from erpnext.selling.page.point_of_sale.point_of_sale import get_items

		result = get_items(
			start=0,
			page_length=6,
			price_list=self.profile.selling_price_list,
			item_group="",
			pos_profile=self.profile.name,
			search_term=description,
		)
		candidates = []
		for row in result.get("items", []):
			item_code = row["item_code"]
			if not frappe.has_permission("Item", doc=item_code):
				continue
			item = frappe.get_doc("Item", item_code)
			if item.disabled or not item.is_sales_item or item.has_variants or item.is_fixed_asset:
				continue
			candidates.append(
				{
					"item_code": item.name,
					"item_name": item.item_name,
					"stock_uom": item.stock_uom,
				}
			)
		return candidates
