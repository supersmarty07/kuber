from dataclasses import dataclass

import frappe

from nepal_retail.localisation.vat import validate_pan
from nepal_retail.voice.contracts import text

NEPAL_CURRENCY = "NPR"
NEPAL_TIMEZONE = "Asia/Kathmandu"
REQUIRED_TAX_TEMPLATES = (
	"Nepal VAT 13%",
	"Nepal VAT Exempt",
	"Nepal Zero Rated",
)


@dataclass(frozen=True)
class LocalisationReport:
	company: str
	currency_ok: bool
	timezone_ok: bool
	pan_ok: bool
	missing_tax_templates: tuple[str, ...]
	issues: tuple[str, ...]

	@property
	def ready(self) -> bool:
		return not self.issues


def verify_company_localisation(company_name: str) -> LocalisationReport:
	company_name = text(company_name, "company", 140)
	frappe.has_permission("Company", doc=company_name, throw=True)
	company = frappe.get_doc("Company", company_name)
	issues: list[str] = []

	currency_ok = getattr(company, "default_currency", None) == NEPAL_CURRENCY
	if not currency_ok:
		issues.append(f"Company default_currency must be {NEPAL_CURRENCY}")

	time_zone = frappe.db.get_single_value("System Settings", "time_zone")
	timezone_ok = time_zone == NEPAL_TIMEZONE
	if not timezone_ok:
		issues.append(f"System Settings time_zone must be {NEPAL_TIMEZONE}")

	pan_ok = False
	try:
		validate_pan(getattr(company, "tax_id", "") or "")
		pan_ok = True
	except ValueError as error:
		issues.append(str(error))

	missing: list[str] = []
	for title in REQUIRED_TAX_TEMPLATES:
		exists = frappe.db.exists("Item Tax Template", {"company": company_name, "title": title})
		if not exists:
			missing.append(title)
	if missing:
		issues.append(f"Missing Item Tax Templates: {', '.join(missing)}")

	return LocalisationReport(
		company=company_name,
		currency_ok=currency_ok,
		timezone_ok=timezone_ok,
		pan_ok=pan_ok,
		missing_tax_templates=tuple(missing),
		issues=tuple(issues),
	)
