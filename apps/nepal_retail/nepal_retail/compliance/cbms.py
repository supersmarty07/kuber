import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date

from nepal_retail.compliance.submission import FiscalSubmission, SubmissionState
from nepal_retail.localisation.calendar import ad_to_bs, fiscal_year_for_date
from nepal_retail.localisation.vat import InvoiceTaxSummary, validate_pan
from nepal_retail.voice.contracts import text
from nepal_retail.voice.providers import requests


class CBMSClientError(RuntimeError):
	pass


def build_cbms_payload(
	*,
	username: str,
	password: str,
	seller_pan: str,
	buyer_pan: str | None,
	buyer_name: str | None,
	posting_date: date,
	fiscal_number: str,
	tax_summary: InvoiceTaxSummary,
	datetime_client: str,
	is_realtime: bool = True,
	is_return: bool = False,
	ref_invoice_number: str | None = None,
	return_reason: str | None = None,
) -> dict:
	if not isinstance(tax_summary, InvoiceTaxSummary):
		raise ValueError("A calculated InvoiceTaxSummary is required for CBMS payload generation")
	validated_seller_pan = validate_pan(seller_pan)
	validated_buyer_pan = validate_pan(buyer_pan) if buyer_pan else ""
	bs_date = ad_to_bs(posting_date).cbms_format()
	fy_cbms = fiscal_year_for_date(posting_date).cbms_label
	cleaned_fiscal_no = text(fiscal_number, "fiscal_number", 60)

	total_sales = (
		tax_summary.taxable_sales + tax_summary.exempt_sales + tax_summary.zero_rated_sales
	)
	payload: dict = {
		"username": text(username, "CBMS username", 100),
		"password": text(password, "CBMS password", 100),
		"seller_pan": validated_seller_pan,
		"buyer_pan": validated_buyer_pan,
		"buyer_name": text(buyer_name, "buyer_name", 140) if buyer_name else "",
		"fiscal_year": fy_cbms,
		"total_sales": float(total_sales),
		"taxable_sales_vat": float(tax_summary.taxable_sales),
		"vat": float(tax_summary.vat_amount),
		"excisable_amount": 0.0,
		"excise": 0.0,
		"health_taxable_amount": 0.0,
		"health_tax": 0.0,
		"export_sales": float(tax_summary.zero_rated_sales),
		"tax_exempted_sales": float(tax_summary.exempt_sales),
		"isrealtime": bool(is_realtime),
		"datetimeclient": text(datetime_client, "datetimeclient", 40),
	}
	if is_return:
		payload["credit_note_number"] = cleaned_fiscal_no
		payload["credit_note_date"] = bs_date
		payload["ref_invoice_number"] = text(ref_invoice_number, "ref_invoice_number", 60)
		payload["reason_for_return"] = text(return_reason, "reason_for_return", 200)
	else:
		payload["invoice_number"] = cleaned_fiscal_no
		payload["invoice_date"] = bs_date
	return payload


class CBMSClient:
	def __init__(self, endpoint_url: str, transport=None):
		cleaned_url = text(endpoint_url, "CBMS endpoint URL", 200)
		if not cleaned_url.startswith("https://"):
			raise ValueError("CBMS endpoint must use HTTPS")
		self.endpoint_url = cleaned_url
		self.transport = transport or requests

	def submit(self, submission: FiscalSubmission) -> FiscalSubmission:
		if submission.state != SubmissionState.PENDING:
			raise ValueError("Only a pending FiscalSubmission can be transmitted to CBMS")
		payload = json.loads(submission.payload_json)
		try:
			with self.transport.post(
				self.endpoint_url,
				json=payload,
				timeout=(5, 30),
				allow_redirects=False,
				stream=True,
			) as response:
				body = bytearray()
				for chunk in response.iter_content(chunk_size=8192):
					body.extend(chunk)
					if len(body) > 65536:
						return submission.mark_unknown()
				if response.status_code >= 500 or response.status_code == 429:
					return submission.mark_unknown()
				if 400 <= response.status_code < 500:
					return replace(submission, state=SubmissionState.REJECTED)
				if not 200 <= response.status_code < 300:
					return submission.mark_unknown()
				parsed = json.loads(body)
		except (requests.RequestException, ValueError):
			return submission.mark_unknown()

		code = parsed.get("code") if isinstance(parsed, dict) else None
		ack_ref = parsed.get("acknowledgement") or (f"CBMS-{code}" if code == 200 else None)
		if code in (200, "200") and ack_ref:
			return submission.acknowledge(str(ack_ref))
		if code in (100, 101, 102, 103, 104):
			return replace(submission, state=SubmissionState.REJECTED)
		return submission.mark_unknown()


class CBMSOutbox:
	def __init__(self):
		self._entries: dict[tuple[str, str, str], FiscalSubmission] = {}
		self._after_commit_callbacks: list[Callable[[], None]] = []

	def get(self, company: str, invoice_type: str, invoice_name: str) -> FiscalSubmission:
		key = (
			text(company, "company", 140),
			text(invoice_type, "invoice type", 140),
			text(invoice_name, "invoice name", 140),
		)
		if key not in self._entries:
			raise KeyError(f"No CBMS outbox entry for {key}")
		return self._entries[key]

	def enqueue_in_transaction(
		self,
		submission: FiscalSubmission,
		*,
		on_after_commit: Callable[[], None] | None = None,
	) -> FiscalSubmission:
		if not isinstance(submission, FiscalSubmission):
			raise ValueError("Expected a FiscalSubmission instance")
		key = (submission.company, submission.invoice_type, submission.invoice_name)
		existing = self._entries.get(key)
		if existing is not None:
			if existing.payload_hash != submission.payload_hash:
				raise ValueError("Conflicting fiscal payload for already-enqueued legal invoice")
			return existing
		self._entries[key] = submission
		if on_after_commit is not None:
			self._after_commit_callbacks.append(on_after_commit)
		return submission

	def rollback_uncommitted(self, company: str, invoice_type: str, invoice_name: str) -> None:
		key = (company, invoice_type, invoice_name)
		self._entries.pop(key, None)
		self._after_commit_callbacks.clear()

	def flush_after_commit_hooks(self) -> int:
		callbacks = list(self._after_commit_callbacks)
		self._after_commit_callbacks.clear()
		for callback in callbacks:
			callback()
		return len(callbacks)

	def dispatch(
		self,
		company: str,
		invoice_type: str,
		invoice_name: str,
		client: CBMSClient,
	) -> FiscalSubmission:
		current = self.get(company, invoice_type, invoice_name)
		if current.state == SubmissionState.ACCEPTED:
			return current
		updated = client.submit(current)
		self._entries[(current.company, current.invoice_type, current.invoice_name)] = updated
		return updated

	def prepare_safe_retry(
		self,
		company: str,
		invoice_type: str,
		invoice_name: str,
		*,
		provider_retry_confirmed_safe: bool = False,
	) -> FiscalSubmission:
		current = self.get(company, invoice_type, invoice_name)
		retried = current.prepare_retry(provider_retry_confirmed_safe=provider_retry_confirmed_safe)
		self._entries[(current.company, current.invoice_type, current.invoice_name)] = retried
		return retried

	def list_pending_reconciliation(self) -> tuple[FiscalSubmission, ...]:
		return tuple(
			entry
			for entry in self._entries.values()
			if entry.state in (SubmissionState.PENDING, SubmissionState.UNKNOWN)
		)
