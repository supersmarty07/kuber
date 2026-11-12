from dataclasses import dataclass
from datetime import date

from nepal_retail.localisation.calendar import fiscal_year_for_date
from nepal_retail.voice.contracts import text


@dataclass(frozen=True)
class FiscalNumberAssignment:
	company: str
	branch_code: str
	fiscal_year: str
	document_kind: str
	sequence_number: int
	fiscal_number: str


@dataclass(frozen=True)
class VoidedFiscalNumber:
	company: str
	branch_code: str
	fiscal_year: str
	fiscal_number: str
	reason: str
	manager_approval: str


def _normalize_branch(branch_code: str) -> str:
	cleaned = text(branch_code, "branch code", 20).upper()
	if not cleaned.isascii() or not cleaned.isalnum():
		raise ValueError("Branch code must be ASCII alphanumeric")
	return cleaned


class FiscalSequenceManager:
	def __init__(self):
		self._counters: dict[tuple[str, str, str, str], int] = {}
		self._assigned: dict[tuple[str, str], FiscalNumberAssignment] = {}
		self._voided: dict[tuple[str, str], VoidedFiscalNumber] = {}

	def next_number(
		self,
		company: str,
		branch_code: str,
		posting_date: date,
		*,
		invoice_name: str,
		is_return: bool = False,
	) -> FiscalNumberAssignment:
		cleaned_company = text(company, "company", 140)
		cleaned_invoice = text(invoice_name, "invoice name", 140)
		invoice_key = (cleaned_company, cleaned_invoice)
		if invoice_key in self._assigned:
			return self._assigned[invoice_key]

		normalized_branch = _normalize_branch(branch_code)
		fy = fiscal_year_for_date(posting_date)
		doc_kind = "CN" if is_return else "TI"
		counter_key = (cleaned_company, normalized_branch, fy.label, doc_kind)
		next_seq = self._counters.get(counter_key, 0) + 1
		self._counters[counter_key] = next_seq
		fiscal_number = f"{doc_kind}-{normalized_branch}-{fy.label}-{next_seq:06d}"
		assignment = FiscalNumberAssignment(
			company=cleaned_company,
			branch_code=normalized_branch,
			fiscal_year=fy.label,
			document_kind=doc_kind,
			sequence_number=next_seq,
			fiscal_number=fiscal_number,
		)
		self._assigned[invoice_key] = assignment
		return assignment

	def record_approved_void(
		self,
		company: str,
		branch_code: str,
		fiscal_year: str,
		fiscal_number: str,
		*,
		reason: str,
		manager_approval: str,
	) -> VoidedFiscalNumber:
		cleaned_company = text(company, "company", 140)
		normalized_branch = _normalize_branch(branch_code)
		cleaned_fy = text(fiscal_year, "fiscal year", 20)
		cleaned_num = text(fiscal_number, "fiscal number", 60)
		void_key = (cleaned_company, cleaned_num)
		if void_key in self._voided:
			return self._voided[void_key]
		void_record = VoidedFiscalNumber(
			company=cleaned_company,
			branch_code=normalized_branch,
			fiscal_year=cleaned_fy,
			fiscal_number=cleaned_num,
			reason=text(reason, "void reason", 200),
			manager_approval=text(manager_approval, "manager approval", 140),
		)
		self._voided[void_key] = void_record
		return void_record
