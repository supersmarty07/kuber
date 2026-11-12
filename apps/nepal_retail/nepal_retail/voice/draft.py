import hashlib
import json
from decimal import Decimal

import frappe

from nepal_retail.localisation.vat import TaxCategory, TaxLineInput, calculate_invoice_taxes, round_npr
from nepal_retail.voice.contracts import DiscountRequest, InvalidOrder, fields, number, text

DEFAULT_MAX_CASHIER_DISCOUNT_PERCENT = Decimal("5")


def _resolve_uom_conversion(item, requested_uom: str) -> Decimal:
	if requested_uom == item.stock_uom:
		return Decimal("1")
	for row in getattr(item, "uoms", None) or []:
		if getattr(row, "uom", None) == requested_uom:
			factor = getattr(row, "conversion_factor", None)
			return number(factor, "conversion_factor", Decimal("0.000001"), Decimal("100000"))
	raise InvalidOrder(f"UOM {requested_uom} is not configured for item {item.name}")


def _resolve_tax_category(item) -> TaxCategory:
	if getattr(item, "is_exempt", False):
		return TaxCategory.EXEMPT
	if getattr(item, "is_zero_rated", False):
		return TaxCategory.ZERO_RATED
	return TaxCategory.TAXABLE


def _lookup_server_rate(item_code: str, price_list: str, requested_uom: str, conversion: Decimal) -> Decimal:
	uom_rate = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": price_list, "uom": requested_uom},
		"price_list_rate",
	)
	if uom_rate is not None:
		return number(uom_rate, "server rate", Decimal("0.01"), Decimal("100000000"))
	stock_rate = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": price_list},
		"price_list_rate",
	)
	if stock_rate is None:
		raise InvalidOrder(f"No server price configured for item {item_code} in {price_list}")
	base = number(stock_rate, "server rate", Decimal("0.01"), Decimal("100000000"))
	return round_npr(base * conversion)


def _validate_discount_authorization(
	discount_payload: dict | None,
	approval_reference: str | None,
	profile,
	gross_net_total: Decimal,
) -> tuple[DiscountRequest | None, str | None]:
	if discount_payload is None:
		return None, None
	fields(discount_payload, ("amount", "kind", "scope"))
	if discount_payload["kind"] not in ("amount", "percent") or discount_payload["scope"] not in (
		"invoice",
		"unspecified",
	):
		raise InvalidOrder("Discount kind or scope is unsupported")
	maximum = Decimal("100") if discount_payload["kind"] == "percent" else Decimal("1000000")
	discount = DiscountRequest(
		number(discount_payload["amount"], "discount", Decimal("0"), maximum),
		discount_payload["kind"],
		discount_payload["scope"],
	)
	if discount.amount == Decimal("0"):
		return None, None
	if not getattr(profile, "allow_discount_change", False):
		frappe.throw("Discounts are not permitted on this POS Profile", frappe.PermissionError)

	max_percent_raw = getattr(profile, "max_discount", None)
	max_percent = (
		number(max_percent_raw, "max_discount", Decimal("0"), Decimal("100"))
		if max_percent_raw is not None
		else DEFAULT_MAX_CASHIER_DISCOUNT_PERCENT
	)
	effective_percent = (
		discount.amount
		if discount.kind == "percent"
		else (discount.amount / gross_net_total * Decimal("100"))
		if gross_net_total > Decimal("0")
		else Decimal("100")
	)
	cleaned_approval = (
		text(approval_reference, "manager_approval_reference", 140)
		if approval_reference is not None
		else None
	)
	if effective_percent > max_percent and not cleaned_approval:
		frappe.throw(
			"Discount exceeds cashier threshold and requires manager approval",
			frappe.PermissionError,
		)
	return discount, cleaned_approval


def build_reviewed_cart_draft(payload: dict, catalogue) -> dict:
	fields(
		payload,
		("items",),
		("customer", "discount", "manager_approval_reference"),
	)
	raw_items = payload["items"]
	if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 50:
		raise InvalidOrder("A reviewed draft requires between 1 and 50 items")

	customer = None
	if payload.get("customer") is not None:
		customer = text(payload["customer"], "customer", 140)
		frappe.has_permission("Customer", doc=customer, throw=True)
		customer_doc = frappe.get_doc("Customer", customer)
		if getattr(customer_doc, "disabled", False):
			raise InvalidOrder("Selected customer is disabled")

	verified_lines: list[dict] = []
	tax_inputs: list[TaxLineInput] = []
	for entry in raw_items:
		fields(entry, ("item_code", "quantity", "uom"))
		item_code = text(entry["item_code"], "item_code", 140)
		quantity = number(entry["quantity"], "quantity", Decimal("0.000001"), Decimal("100000"))
		requested_uom = text(entry["uom"], "uom", 40)

		frappe.has_permission("Item", doc=item_code, throw=True)
		item = frappe.get_doc("Item", item_code)
		if item.disabled or not item.is_sales_item or item.has_variants or item.is_fixed_asset:
			raise InvalidOrder(f"Item {item_code} is not eligible for POS sale")
		conversion = _resolve_uom_conversion(item, requested_uom)
		unit_rate = _lookup_server_rate(
			item.name,
			catalogue.profile.selling_price_list,
			requested_uom,
			conversion,
		)
		category = _resolve_tax_category(item)
		verified_lines.append(
			{
				"item_code": item.name,
				"item_name": item.item_name,
				"uom": requested_uom,
				"stock_uom": item.stock_uom,
				"conversion_factor": str(conversion),
				"quantity": str(quantity),
				"server_rate": str(unit_rate),
				"tax_category": category.value,
			}
		)
		tax_inputs.append(
			TaxLineInput(
				item_code=item.name,
				quantity=quantity,
				rate=unit_rate,
				category=category,
			)
		)

	prices_include_tax = bool(getattr(catalogue.profile, "taxes_and_charges_included", False))
	pre_discount_summary = calculate_invoice_taxes(
		tax_inputs,
		prices_include_tax=prices_include_tax,
	)
	discount, approval_ref = _validate_discount_authorization(
		payload.get("discount"),
		payload.get("manager_approval_reference"),
		catalogue.profile,
		pre_discount_summary.gross_net_total,
	)
	summary = calculate_invoice_taxes(
		tax_inputs,
		discount=discount,
		prices_include_tax=prices_include_tax,
	)
	for line_dict, calc_line in zip(verified_lines, summary.lines, strict=True):
		line_dict["net_amount"] = str(calc_line.net_amount)
		line_dict["vat_amount"] = str(calc_line.vat_amount)
		line_dict["total_amount"] = str(calc_line.total_amount)

	revision_source = {
		"company": catalogue.profile.company,
		"pos_profile": catalogue.profile.name,
		"customer": customer,
		"items": verified_lines,
		"discount_total": str(summary.discount_total),
		"vat_amount": str(summary.vat_amount),
		"grand_total": str(summary.grand_total),
	}
	order_revision = hashlib.sha256(
		json.dumps(revision_source, sort_keys=True, separators=(",", ":")).encode()
	).hexdigest()

	return {
		"status": "draft_ready",
		"can_submit": False,
		"requires_manual_checkout": True,
		"company": catalogue.profile.company,
		"warehouse": catalogue.profile.warehouse,
		"pos_profile": catalogue.profile.name,
		"customer": customer,
		"items": verified_lines,
		"discount_total": str(summary.discount_total),
		"manager_approval_reference": approval_ref,
		"taxable_sales": str(summary.taxable_sales),
		"exempt_sales": str(summary.exempt_sales),
		"zero_rated_sales": str(summary.zero_rated_sales),
		"vat_amount": str(summary.vat_amount),
		"grand_total": str(summary.grand_total),
		"order_revision": order_revision,
	}
