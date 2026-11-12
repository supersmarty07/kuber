from decimal import Decimal
from enum import Enum

from nepal_retail.voice.contracts import number, text


class LegalInvoiceMode(str, Enum):
	SALES_INVOICE = "Sales Invoice"
	POS_INVOICE = "POS Invoice"


IMMUTABLE_FISCAL_FIELDS = (
	"company",
	"customer",
	"tax_id",
	"posting_date",
	"is_return",
	"return_against",
	"net_total",
	"total_taxes_and_charges",
	"discount_amount",
	"grand_total",
	"cbms_fiscal_number",
	"cbms_fiscal_year",
)


def should_emit_cbms_for_invoice(
	doc,
	*,
	configured_mode: LegalInvoiceMode = LegalInvoiceMode.SALES_INVOICE,
) -> bool:
	doctype = getattr(doc, "doctype", None)
	docstatus = getattr(doc, "docstatus", 0)
	if docstatus != 1:
		return False
	if configured_mode == LegalInvoiceMode.POS_INVOICE:
		if doctype == "POS Invoice":
			return True
		if doctype == "Sales Invoice" and not getattr(doc, "is_consolidated", False):
			return True
		return False
	if configured_mode == LegalInvoiceMode.SALES_INVOICE:
		return doctype == "Sales Invoice"
	raise ValueError(f"Unsupported legal invoice mode: {configured_mode}")


def _extract_item_signature(doc) -> tuple[tuple[str, str, str], ...]:
	rows = getattr(doc, "items", None) or []
	return tuple(
		(
			str(getattr(row, "item_code", "")),
			str(getattr(row, "qty", "")),
			str(getattr(row, "rate", "")),
		)
		for row in rows
	)


def enforce_fiscal_immutability(current_doc, previous_doc) -> None:
	if previous_doc is None:
		return
	was_fiscalized = bool(
		getattr(previous_doc, "docstatus", 0) == 1
		or getattr(previous_doc, "cbms_fiscal_number", None)
	)
	if not was_fiscalized:
		return
	for field in IMMUTABLE_FISCAL_FIELDS:
		if getattr(current_doc, field, None) != getattr(previous_doc, field, None):
			raise PermissionError(
				f"Fiscalized invoice field '{field}' is immutable; issue an approved Credit Note instead"
			)
	if _extract_item_signature(current_doc) != _extract_item_signature(previous_doc):
		raise PermissionError(
			"Fiscalized invoice line items are immutable; issue an approved Credit Note instead"
		)


def block_fiscal_cancel_delete_rename(doc, action: str) -> None:
	normalized = text(action, "action", 40).lower()
	if normalized not in ("cancel", "trash", "delete", "rename"):
		raise ValueError(f"Unsupported protection action: {action}")
	is_fiscalized = bool(
		getattr(doc, "docstatus", 0) == 1 or getattr(doc, "cbms_fiscal_number", None)
	)
	if is_fiscalized:
		raise PermissionError(
			f"Cannot {normalized} a fiscalized invoice; issue an approved Credit Note / return invoice"
		)


def validate_credit_note(credit_note_doc, original_invoice_doc) -> None:
	if not getattr(credit_note_doc, "is_return", False):
		raise ValueError("Credit note document must have is_return enabled")
	return_against = getattr(credit_note_doc, "return_against", None)
	if not return_against or original_invoice_doc is None:
		raise ValueError("Credit note must reference an existing original invoice")
	if getattr(original_invoice_doc, "name", None) != return_against:
		raise ValueError("Original invoice name does not match return_against")
	if getattr(original_invoice_doc, "docstatus", 0) != 1:
		raise ValueError("Credit note can only be issued against a submitted fiscal invoice")
	if getattr(original_invoice_doc, "is_return", False):
		raise ValueError("Cannot issue a credit note against another credit note")
	if getattr(credit_note_doc, "company", None) != getattr(original_invoice_doc, "company", None):
		raise ValueError("Credit note company must match original invoice company")
	if getattr(credit_note_doc, "customer", None) != getattr(original_invoice_doc, "customer", None):
		raise ValueError("Credit note customer must match original invoice customer")
	text(getattr(credit_note_doc, "return_reason", None), "return_reason", 200)

	cn_total = abs(
		number(
			getattr(credit_note_doc, "grand_total", 0),
			"credit note grand_total",
			Decimal("-100000000"),
			Decimal("100000000"),
		)
	)
	orig_total = number(
		getattr(original_invoice_doc, "grand_total", 0),
		"original grand_total",
		Decimal("0.01"),
		Decimal("100000000"),
	)
	if cn_total == Decimal("0") or cn_total > orig_total:
		raise ValueError("Credit note total must be positive and cannot exceed original invoice total")
