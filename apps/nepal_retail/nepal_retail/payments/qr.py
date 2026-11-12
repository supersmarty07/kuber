import hashlib
import hmac
from dataclasses import dataclass

from nepal_retail.payments.repository import PaymentRecord, PaymentRepository
from nepal_retail.payments.state import PaymentIntent, PaymentState
from nepal_retail.voice.contracts import fields, text

MAX_CALLBACK_SKEW_SECONDS = 300


def _sign_fields(secret_key: str, parts: tuple[str, ...]) -> str:
	key = text(secret_key, "secret key", 200).encode()
	message = "|".join(parts).encode()
	return hmac.new(key, message, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class DynamicQRPayload:
	provider: str
	merchant_id: str
	intent_id: str
	company: str
	order_revision: str
	amount_minor: int
	currency: str
	expires_at: int
	signature: str


def build_dynamic_qr(
	provider: str,
	merchant_id: str,
	secret_key: str,
	intent: PaymentIntent,
	*,
	now: int,
	ttl_seconds: int = 300,
) -> DynamicQRPayload:
	if not isinstance(intent, PaymentIntent) or intent.state != PaymentState.PROCESSING:
		raise ValueError("Dynamic QR requires an active processing PaymentIntent")
	if isinstance(now, bool) or not isinstance(now, int) or now <= 0:
		raise ValueError("Current timestamp must be a positive integer")
	if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 60 <= ttl_seconds <= 900:
		raise ValueError("QR TTL must be between 60 and 900 seconds")
	cleaned_provider = text(provider, "provider", 60)
	cleaned_merchant = text(merchant_id, "merchant ID", 140)
	expires_at = now + ttl_seconds
	signature = _sign_fields(
		secret_key,
		(
			cleaned_provider,
			cleaned_merchant,
			intent.intent_id,
			intent.order_revision,
			str(intent.amount_minor),
			intent.currency,
			str(expires_at),
		),
	)
	return DynamicQRPayload(
		provider=cleaned_provider,
		merchant_id=cleaned_merchant,
		intent_id=intent.intent_id,
		company=intent.company,
		order_revision=intent.order_revision,
		amount_minor=intent.amount_minor,
		currency=intent.currency,
		expires_at=expires_at,
		signature=signature,
	)


def sign_qr_callback(
	secret_key: str,
	*,
	provider: str,
	event_id: str,
	merchant_id: str,
	intent_id: str,
	order_revision: str,
	amount_minor: int,
	currency: str,
	provider_reference: str,
	timestamp: int,
) -> str:
	return _sign_fields(
		secret_key,
		(
			text(provider, "provider", 60),
			text(event_id, "event ID", 140),
			text(merchant_id, "merchant ID", 140),
			text(intent_id, "intent ID", 140),
			text(order_revision, "order revision", 140),
			str(amount_minor),
			text(currency, "currency", 10),
			text(provider_reference, "provider reference", 140),
			str(timestamp),
		),
	)


def verify_and_record_qr_callback(
	repository: PaymentRepository,
	*,
	provider: str,
	merchant_id: str,
	secret_key: str,
	callback_payload: dict,
	now: int,
) -> PaymentRecord:
	try:
		fields(
			callback_payload,
			(
				"event_id",
				"merchant_id",
				"intent_id",
				"order_revision",
				"amount_minor",
				"currency",
				"provider_reference",
				"timestamp",
				"signature",
			),
		)
	except ValueError as error:
		raise ValueError("Invalid QR callback payload shape") from error

	cleaned_provider = text(provider, "provider", 60)
	expected_merchant = text(merchant_id, "merchant ID", 140)
	if callback_payload["merchant_id"] != expected_merchant:
		raise ValueError("QR callback merchant ID does not match configured store merchant")

	timestamp = callback_payload["timestamp"]
	if isinstance(timestamp, bool) or not isinstance(timestamp, int):
		raise ValueError("QR callback timestamp must be an integer")
	if abs(now - timestamp) > MAX_CALLBACK_SKEW_SECONDS:
		raise ValueError("QR callback timestamp is outside the allowed replay window")

	amount_minor = callback_payload["amount_minor"]
	if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
		raise ValueError("QR callback amount_minor must be an integer")

	expected_sig = sign_qr_callback(
		secret_key,
		provider=cleaned_provider,
		event_id=callback_payload["event_id"],
		merchant_id=callback_payload["merchant_id"],
		intent_id=callback_payload["intent_id"],
		order_revision=callback_payload["order_revision"],
		amount_minor=amount_minor,
		currency=callback_payload["currency"],
		provider_reference=callback_payload["provider_reference"],
		timestamp=timestamp,
	)
	supplied_sig = callback_payload["signature"]
	if not isinstance(supplied_sig, str) or not hmac.compare_digest(expected_sig, supplied_sig):
		raise ValueError("QR callback signature verification failed")

	record = repository.get(callback_payload["intent_id"])
	if record.intent.order_revision != callback_payload["order_revision"]:
		raise ValueError("QR callback order revision does not match payment intent")

	return repository.apply_verified_event(
		provider=cleaned_provider,
		event_id=callback_payload["event_id"],
		intent_id=callback_payload["intent_id"],
		amount_minor=amount_minor,
		currency=callback_payload["currency"],
		provider_reference=callback_payload["provider_reference"],
	)
