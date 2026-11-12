from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from nepal_retail.voice.contracts import DiscountRequest, number

VAT_RATE = Decimal("0.13")
PAISA = Decimal("0.01")


class TaxCategory(str, Enum):
	TAXABLE = "taxable"
	EXEMPT = "exempt"
	ZERO_RATED = "zero_rated"


@dataclass(frozen=True)
class TaxLineInput:
	item_code: str
	quantity: Decimal
	rate: Decimal
	category: TaxCategory = TaxCategory.TAXABLE


@dataclass(frozen=True)
class CalculatedTaxLine:
	item_code: str
	quantity: Decimal
	unit_net_rate: Decimal
	gross_net_amount: Decimal
	discount_allocated: Decimal
	net_amount: Decimal
	vat_amount: Decimal
	total_amount: Decimal
	category: TaxCategory


@dataclass(frozen=True)
class InvoiceTaxSummary:
	lines: tuple[CalculatedTaxLine, ...]
	gross_net_total: Decimal
	discount_total: Decimal
	taxable_sales: Decimal
	exempt_sales: Decimal
	zero_rated_sales: Decimal
	vat_amount: Decimal
	grand_total: Decimal


def round_npr(value: Decimal) -> Decimal:
	return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def validate_pan(pan: str) -> str:
	if not isinstance(pan, str):
		raise ValueError("PAN/VAT number must be a 9-digit string")
	cleaned = pan.strip()
	if len(cleaned) != 9 or not cleaned.isascii() or not cleaned.isdigit() or cleaned == "000000000":
		raise ValueError("Nepal PAN/VAT number must be exactly 9 ASCII digits")
	return cleaned


def parse_tax_line(raw: TaxLineInput | dict) -> TaxLineInput:
	if isinstance(raw, TaxLineInput):
		item_code = raw.item_code
		qty = number(raw.quantity, "quantity", Decimal("0.000001"), Decimal("100000"))
		rate = number(raw.rate, "rate", Decimal("0"), Decimal("100000000"))
		category = raw.category
	elif isinstance(raw, dict):
		item_code = raw.get("item_code")
		qty = number(raw.get("quantity"), "quantity", Decimal("0.000001"), Decimal("100000"))
		rate = number(raw.get("rate"), "rate", Decimal("0"), Decimal("100000000"))
		category = raw.get("category", TaxCategory.TAXABLE)
	else:
		raise ValueError("Each tax line must be a TaxLineInput or mapping")
	if not isinstance(item_code, str) or not item_code.strip():
		raise ValueError("Each tax line requires an item_code")
	try:
		category_enum = TaxCategory(category)
	except ValueError as error:
		raise ValueError(f"Unsupported tax category: {category}") from error
	return TaxLineInput(item_code.strip(), qty, rate, category_enum)


def calculate_invoice_taxes(
	lines: list[TaxLineInput | dict] | tuple[TaxLineInput | dict, ...],
	*,
	discount: DiscountRequest | dict | None = None,
	prices_include_tax: bool = False,
) -> InvoiceTaxSummary:
	if not isinstance(lines, (list, tuple)) or not 1 <= len(lines) <= 50:
		raise ValueError("Tax calculation requires between 1 and 50 lines")
	parsed_lines = [parse_tax_line(line) for line in lines]
	unit_nets: list[Decimal] = []
	gross_nets: list[Decimal] = []
	for line in parsed_lines:
		if prices_include_tax and line.category == TaxCategory.TAXABLE:
			unit_net = line.rate / (Decimal("1") + VAT_RATE)
		else:
			unit_net = line.rate
		unit_nets.append(unit_net.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
		gross_nets.append(round_npr(unit_net * line.quantity))

	gross_net_total = sum(gross_nets, Decimal("0.00"))
	discount_total = Decimal("0.00")
	if discount is not None:
		if isinstance(discount, dict):
			kind = discount.get("kind")
			amount = number(discount.get("amount"), "discount", Decimal("0"), Decimal("100000000"))
		elif isinstance(discount, DiscountRequest):
			kind = discount.kind
			amount = discount.amount
		else:
			raise ValueError("Unsupported discount format")
		if kind == "percent":
			if amount > Decimal("100"):
				raise ValueError("Percentage discount cannot exceed 100")
			discount_total = round_npr(gross_net_total * (amount / Decimal("100")))
		elif kind == "amount":
			discount_total = round_npr(amount)
		else:
			raise ValueError("Discount kind must be amount or percent")
		if discount_total > gross_net_total:
			raise ValueError("Discount cannot exceed gross invoice net total")

	remaining_discount = discount_total
	calculated: list[CalculatedTaxLine] = []
	for index, (line, unit_net, gross_net) in enumerate(
		zip(parsed_lines, unit_nets, gross_nets, strict=True)
	):
		if discount_total == Decimal("0.00") or gross_net_total == Decimal("0.00"):
			allocated = Decimal("0.00")
		elif index == len(parsed_lines) - 1:
			allocated = remaining_discount
		else:
			allocated = min(
				remaining_discount,
				round_npr(discount_total * (gross_net / gross_net_total)),
			)
			remaining_discount -= allocated
		net_amount = gross_net - allocated
		vat_amount = (
			round_npr(net_amount * VAT_RATE)
			if line.category == TaxCategory.TAXABLE
			else Decimal("0.00")
		)
		calculated.append(
			CalculatedTaxLine(
				item_code=line.item_code,
				quantity=line.quantity,
				unit_net_rate=unit_net,
				gross_net_amount=gross_net,
				discount_allocated=allocated,
				net_amount=net_amount,
				vat_amount=vat_amount,
				total_amount=net_amount + vat_amount,
				category=line.category,
			)
		)

	taxable_sales = sum(
		(item.net_amount for item in calculated if item.category == TaxCategory.TAXABLE),
		Decimal("0.00"),
	)
	exempt_sales = sum(
		(item.net_amount for item in calculated if item.category == TaxCategory.EXEMPT),
		Decimal("0.00"),
	)
	zero_rated_sales = sum(
		(item.net_amount for item in calculated if item.category == TaxCategory.ZERO_RATED),
		Decimal("0.00"),
	)
	vat_total = round_npr(taxable_sales * VAT_RATE)
	grand_total = taxable_sales + exempt_sales + zero_rated_sales + vat_total
	return InvoiceTaxSummary(
		lines=tuple(calculated),
		gross_net_total=gross_net_total,
		discount_total=discount_total,
		taxable_sales=taxable_sales,
		exempt_sales=exempt_sales,
		zero_rated_sales=zero_rated_sales,
		vat_amount=vat_total,
		grand_total=grand_total,
	)
