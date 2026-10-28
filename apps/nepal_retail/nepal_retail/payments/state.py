from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum

from nepal_retail.voice.contracts import number, text


class PaymentState(str, Enum):
	PENDING = "pending"
	PROCESSING = "processing"
	UNKNOWN = "unknown"
	PAID = "paid"
	FAILED = "failed"
	EXPIRED = "expired"


@dataclass(frozen=True)
class PaymentIntent:
	intent_id: str
	company: str
	order_revision: str
	amount_minor: int
	currency: str = "NPR"
	state: PaymentState = PaymentState.PENDING
	provider_reference: str | None = None

	@classmethod
	def create(cls, intent_id, company, order_revision, amount):
		value = number(amount, "payment amount", Decimal("0.01"), Decimal("1000000000"))
		minor = value * 100
		if minor != minor.to_integral_value():
			raise ValueError("NPR payments support at most two decimal places")
		return cls(
			text(intent_id, "intent ID", 140),
			text(company, "company", 140),
			text(order_revision, "order revision", 140),
			int(minor),
		)

	def start(self, order_revision):
		if self.state != PaymentState.PENDING or order_revision != self.order_revision:
			raise ValueError("Only an unchanged pending order can start a payment")
		return replace(self, state=PaymentState.PROCESSING)

	def mark_unknown(self):
		if self.state != PaymentState.PROCESSING:
			raise ValueError("Only an in-progress payment can become unknown")
		return replace(self, state=PaymentState.UNKNOWN)

	def record_verified_success(self, amount_minor, currency, provider_reference):
		if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
			raise ValueError("A provider amount must use integer minor units")
		if amount_minor != self.amount_minor or currency != self.currency:
			raise ValueError("Provider amount or currency does not match the payment intent")
		reference = text(provider_reference, "provider reference", 140)
		if self.state == PaymentState.PAID and reference == self.provider_reference:
			return self
		if self.state not in (PaymentState.PROCESSING, PaymentState.UNKNOWN):
			raise ValueError("Payment result conflicts with the current state; reconciliation is required")
		return replace(self, state=PaymentState.PAID, provider_reference=reference)

	def close_unpaid(self, state):
		if state not in (PaymentState.FAILED, PaymentState.EXPIRED) or self.state != PaymentState.PENDING:
			raise ValueError("An initiated or paid transaction requires provider reconciliation")
		return replace(self, state=state)
