import unittest
from decimal import Decimal

from nepal_retail.voice.contracts import InvalidOrder, parse_order


class OrderContractTests(unittest.TestCase):
	def order(self, **changes):
		return {"items": [{"description": "wireless mouse", "quantity": "1"}], **changes}

	def test_valid_order_preserves_decimal_and_requested_discount(self):
		order = parse_order(
			self.order(customer="Ram", discount={"amount": "500", "kind": "amount", "scope": "unspecified"})
		)
		self.assertEqual(order.items[0].quantity, Decimal("1"))
		self.assertEqual(order.discount.scope, "unspecified")

	def test_financial_and_action_fields_are_rejected(self):
		for field in ("total", "taxes", "submit", "payment", "item_code", "rate"):
			with self.subTest(field=field), self.assertRaises(InvalidOrder):
				parse_order(self.order(**{field: 1}))
		for field in ("item_code", "rate", "warehouse"):
			with self.subTest(field=field), self.assertRaises(InvalidOrder):
				parse_order({"items": [{"description": "mouse", "quantity": 1, field: "injected"}]})

	def test_invalid_quantities_are_rejected(self):
		for quantity in (True, None, 0, -1, "NaN", "Infinity", "0.0000001", 100001, {}, []):
			with self.subTest(quantity=quantity), self.assertRaises(InvalidOrder):
				parse_order({"items": [{"description": "mouse", "quantity": quantity}]})

	def test_invalid_shapes_are_rejected(self):
		for payload in (None, [], {}, self.order(items=[]), self.order(items=[{}]), self.order(customer=" ")):
			with self.subTest(payload=payload), self.assertRaises(InvalidOrder):
				parse_order(payload)

	def test_invalid_discounts_are_rejected(self):
		for discount in (
			{"amount": 101, "kind": "percent", "scope": "invoice"},
			{"amount": -1, "kind": "amount", "scope": "invoice"},
			{"amount": 5, "kind": "amount", "scope": "line"},
		):
			with self.subTest(discount=discount), self.assertRaises(InvalidOrder):
				parse_order(self.order(discount=discount))


if __name__ == "__main__":
	unittest.main()
