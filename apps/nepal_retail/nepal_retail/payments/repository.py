from dataclasses import dataclass, replace

from nepal_retail.payments.state import PaymentIntent, PaymentState
from nepal_retail.voice.contracts import text


@dataclass(frozen=True)
class PaymentRecord:
	intent: PaymentIntent
	invoice_name: str | None = None
	invoice_issue_failed: bool = False
	invoice_failure_reason: str | None = None
	settlement_reference: str | None = None
	refunded_amount_minor: int = 0
	refund_reference: str | None = None


@dataclass(frozen=True)
class ProcessedPaymentEvent:
	provider: str
	event_id: str
	intent_id: str
	amount_minor: int
	currency: str
	provider_reference: str


class PaymentRepository:
	def __init__(self):
		self._records: dict[str, PaymentRecord] = {}
		self._events: dict[tuple[str, str], ProcessedPaymentEvent] = {}

	def get(self, intent_id: str) -> PaymentRecord:
		cleaned = text(intent_id, "intent ID", 140)
		if cleaned not in self._records:
			raise KeyError(f"Unknown payment intent: {cleaned}")
		return self._records[cleaned]

	def create_intent(self, intent_id: str, company: str, order_revision: str, amount) -> PaymentRecord:
		intent = PaymentIntent.create(intent_id, company, order_revision, amount)
		if intent.intent_id in self._records:
			raise ValueError(f"Payment intent {intent.intent_id} already exists")
		for existing in self._records.values():
			if existing.intent.company == intent.company and existing.intent.order_revision == intent.order_revision:
				if existing.intent.state in (
					PaymentState.PROCESSING,
					PaymentState.UNKNOWN,
					PaymentState.PAID,
				):
					raise ValueError(
						"Order revision already has an active, unknown or paid payment intent; reconcile first"
					)
		record = PaymentRecord(intent=intent)
		self._records[intent.intent_id] = record
		return record

	def start_payment(self, intent_id: str, order_revision: str) -> PaymentRecord:
		record = self.get(intent_id)
		for existing in self._records.values():
			if (
				existing.intent.intent_id != record.intent.intent_id
				and existing.intent.company == record.intent.company
				and existing.intent.order_revision == order_revision
				and existing.intent.state in (PaymentState.PROCESSING, PaymentState.UNKNOWN, PaymentState.PAID)
			):
				raise ValueError("Another payment intent for this order revision is already active or paid")
		updated = replace(record, intent=record.intent.start(order_revision))
		self._records[record.intent.intent_id] = updated
		return updated

	def mark_unknown(self, intent_id: str) -> PaymentRecord:
		record = self.get(intent_id)
		updated = replace(record, intent=record.intent.mark_unknown())
		self._records[record.intent.intent_id] = updated
		return updated

	def close_unpaid(self, intent_id: str, state: PaymentState) -> PaymentRecord:
		record = self.get(intent_id)
		updated = replace(record, intent=record.intent.close_unpaid(state))
		self._records[record.intent.intent_id] = updated
		return updated

	def apply_verified_event(
		self,
		*,
		provider: str,
		event_id: str,
		intent_id: str,
		amount_minor: int,
		currency: str,
		provider_reference: str,
	) -> PaymentRecord:
		provider_key = text(provider, "provider", 60)
		event_key = text(event_id, "event ID", 140)
		dedup_key = (provider_key, event_key)
		existing_event = self._events.get(dedup_key)
		if existing_event is not None:
			if (
				existing_event.intent_id != intent_id
				or existing_event.amount_minor != amount_minor
				or existing_event.currency != currency
				or existing_event.provider_reference != provider_reference
			):
				raise ValueError("Conflicting payload for already-processed provider event ID")
			return self.get(intent_id)

		record = self.get(intent_id)
		paid_intent = record.intent.record_verified_success(amount_minor, currency, provider_reference)
		updated = replace(record, intent=paid_intent)
		self._records[record.intent.intent_id] = updated
		self._events[dedup_key] = ProcessedPaymentEvent(
			provider=provider_key,
			event_id=event_key,
			intent_id=record.intent.intent_id,
			amount_minor=amount_minor,
			currency=currency,
			provider_reference=paid_intent.provider_reference,
		)
		return updated

	def record_invoice_failure(self, intent_id: str, reason: str) -> PaymentRecord:
		record = self.get(intent_id)
		if record.intent.state != PaymentState.PAID:
			raise ValueError("Only a paid intent can record a post-payment invoice failure")
		if record.invoice_name is not None:
			raise ValueError("Invoice is already attached to this paid intent")
		updated = replace(
			record,
			invoice_issue_failed=True,
			invoice_failure_reason=text(reason, "invoice failure reason", 200),
		)
		self._records[record.intent.intent_id] = updated
		return updated

	def attach_issued_invoice(self, intent_id: str, invoice_name: str) -> PaymentRecord:
		record = self.get(intent_id)
		if record.intent.state != PaymentState.PAID:
			raise ValueError("Cannot attach an invoice to an unpaid payment intent")
		cleaned_invoice = text(invoice_name, "invoice name", 140)
		if record.invoice_name == cleaned_invoice:
			return record
		if record.invoice_name is not None:
			raise ValueError("Payment intent is already bound to a different invoice")
		updated = replace(
			record,
			invoice_name=cleaned_invoice,
			invoice_issue_failed=False,
			invoice_failure_reason=None,
		)
		self._records[record.intent.intent_id] = updated
		return updated

	def reconcile_settlement(
		self,
		intent_id: str,
		*,
		settled_amount_minor: int,
		currency: str,
		provider_reference: str,
		settlement_reference: str,
	) -> PaymentRecord:
		record = self.get(intent_id)
		cleaned_settlement = text(settlement_reference, "settlement reference", 140)
		paid_intent = record.intent.record_verified_success(
			settled_amount_minor,
			currency,
			provider_reference,
		)
		if (
			record.settlement_reference is not None
			and record.settlement_reference != cleaned_settlement
		):
			raise ValueError("Conflicting settlement reference for payment intent")
		updated = replace(record, intent=paid_intent, settlement_reference=cleaned_settlement)
		self._records[record.intent.intent_id] = updated
		return updated

	def record_authorized_refund(
		self,
		intent_id: str,
		*,
		refund_amount_minor: int,
		refund_reference: str,
		manager_approval: str,
	) -> PaymentRecord:
		record = self.get(intent_id)
		if record.intent.state != PaymentState.PAID:
			raise ValueError("Only a paid payment intent can be refunded")
		if isinstance(refund_amount_minor, bool) or not isinstance(refund_amount_minor, int) or refund_amount_minor <= 0:
			raise ValueError("Refund amount must be a positive integer in minor units")
		text(manager_approval, "manager approval", 140)
		cleaned_ref = text(refund_reference, "refund reference", 140)
		if record.refund_reference == cleaned_ref:
			return record
		new_total = record.refunded_amount_minor + refund_amount_minor
		if new_total > record.intent.amount_minor:
			raise ValueError("Cumulative refund cannot exceed the paid amount")
		updated = replace(
			record,
			refunded_amount_minor=new_total,
			refund_reference=cleaned_ref,
		)
		self._records[record.intent.intent_id] = updated
		return updated
