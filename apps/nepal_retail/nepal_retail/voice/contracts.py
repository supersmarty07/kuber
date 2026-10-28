from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class InvalidOrder(ValueError):
	pass


def text(value, field, maximum=200):
	if not isinstance(value, str) or not value.strip() or len(value) > maximum:
		raise InvalidOrder(f"{field} must be nonempty text of at most {maximum} characters")
	return value.strip()


def number(value, field, minimum, maximum):
	if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
		raise InvalidOrder(f"{field} must be a decimal number")
	try:
		result = Decimal(str(value))
	except InvalidOperation as error:
		raise InvalidOrder(f"{field} must be a decimal number") from error
	if not result.is_finite() or not minimum <= result <= maximum:
		raise InvalidOrder(f"{field} is outside the permitted range")
	if result.as_tuple().exponent < -6:
		raise InvalidOrder(f"{field} supports at most six decimal places")
	return result


def fields(value, required, optional=()):
	if not isinstance(value, dict):
		raise InvalidOrder("Expected an object")
	if set(value) - set(required) - set(optional) or set(required) - set(value):
		raise InvalidOrder("Missing or unsupported fields")


@dataclass(frozen=True)
class OrderLine:
	description: str
	quantity: Decimal
	uom: str | None


@dataclass(frozen=True)
class DiscountRequest:
	amount: Decimal
	kind: str
	scope: str


@dataclass(frozen=True)
class VoiceOrder:
	items: tuple[OrderLine, ...]
	customer: str | None
	discount: DiscountRequest | None


def parse_order(payload):
	fields(payload, ("items",), ("customer", "discount"))
	if not isinstance(payload["items"], list) or not 1 <= len(payload["items"]) <= 50:
		raise InvalidOrder("An order needs between 1 and 50 items")
	items = []
	for item in payload["items"]:
		fields(item, ("description", "quantity"), ("uom",))
		items.append(
			OrderLine(
				text(item["description"], "description"),
				number(item["quantity"], "quantity", Decimal("0.000001"), Decimal("100000")),
				text(item["uom"], "uom", 40) if item.get("uom") is not None else None,
			)
		)
	customer = text(payload["customer"], "customer") if payload.get("customer") is not None else None
	discount = None
	if payload.get("discount") is not None:
		request = payload["discount"]
		fields(request, ("amount", "kind", "scope"))
		if request["kind"] not in ("amount", "percent") or request["scope"] not in ("invoice", "unspecified"):
			raise InvalidOrder("Discount kind or scope is unsupported")
		maximum = Decimal("100") if request["kind"] == "percent" else Decimal("1000000")
		discount = DiscountRequest(
			number(request["amount"], "discount", Decimal("0"), maximum),
			request["kind"],
			request["scope"],
		)
	return VoiceOrder(tuple(items), customer, discount)
