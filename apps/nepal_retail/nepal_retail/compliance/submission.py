import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum

from nepal_retail.voice.contracts import text


class SubmissionState(str, Enum):
	PENDING = "pending"
	UNKNOWN = "unknown"
	ACCEPTED = "accepted"
	REJECTED = "rejected"


@dataclass(frozen=True)
class FiscalSubmission:
	company: str
	invoice_type: str
	invoice_name: str
	payload_json: str
	payload_hash: str
	state: SubmissionState = SubmissionState.PENDING
	acknowledgement: str | None = None

	@classmethod
	def create(cls, company, invoice_type, invoice_name, official_payload):
		if not isinstance(official_payload, dict) or not official_payload:
			raise ValueError("A payload built from the official CBMS specification is required")
		payload = json.dumps(
			official_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
		)
		if len(payload.encode()) > 1024 * 1024:
			raise ValueError("Fiscal payload exceeds the size limit")
		return cls(
			text(company, "company", 140),
			text(invoice_type, "invoice type", 140),
			text(invoice_name, "invoice name", 140),
			payload,
			hashlib.sha256(payload.encode()).hexdigest(),
		)

	def mark_unknown(self):
		if self.state != SubmissionState.PENDING:
			raise ValueError("Only a pending submission can have an unknown outcome")
		return replace(self, state=SubmissionState.UNKNOWN)

	def acknowledge(self, reference):
		reference = text(reference, "acknowledgement", 200)
		if self.state == SubmissionState.ACCEPTED and reference == self.acknowledgement:
			return self
		if self.state not in (SubmissionState.PENDING, SubmissionState.UNKNOWN):
			raise ValueError("Conflicting CBMS response requires reconciliation")
		return replace(self, state=SubmissionState.ACCEPTED, acknowledgement=reference)

	def prepare_retry(self, *, provider_retry_confirmed_safe=False):
		if self.state != SubmissionState.UNKNOWN or provider_retry_confirmed_safe is not True:
			raise ValueError("Resolve the previous CBMS outcome before retrying")
		return replace(self, state=SubmissionState.PENDING)
