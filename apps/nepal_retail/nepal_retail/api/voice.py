import time

import frappe

from nepal_retail.voice.catalogue import POSCatalogue
from nepal_retail.voice.contracts import InvalidOrder
from nepal_retail.voice.preview import preview_order
from nepal_retail.voice.providers import OpenAIProvider, ProviderUnavailable


def authorize(pos_profile, hosted=False):
	if frappe.session.user == "Guest":
		frappe.throw("Authentication is required", frappe.PermissionError)
	if frappe.conf.get("nepal_retail_preview_enabled") is not True:
		frappe.throw("Nepal Retail preview is disabled", frappe.PermissionError)
	if hosted and frappe.conf.get("nepal_retail_hosted_voice_enabled") is not True:
		frappe.throw("Hosted voice processing is disabled", frappe.PermissionError)
	frappe.has_permission("POS Invoice", ptype="create", throw=True)
	catalogue = POSCatalogue(pos_profile)
	cache = frappe.cache()
	key = f"nepal_retail:voice:{frappe.local.site}:{frappe.session.user}:{int(time.time()) // 60}"
	count = cache.incr(key)
	cache.expire(key, 120)
	if count > 10:
		frappe.throw("Voice request limit reached; try again next minute", frappe.ValidationError)
	return catalogue


def provider():
	return OpenAIProvider(
		frappe.conf.get("nepal_retail_openai_api_key"),
		frappe.conf.get("nepal_retail_extraction_model"),
	)


@frappe.whitelist(methods=["POST"])
def preview(pos_profile, order):
	catalogue = authorize(pos_profile)
	try:
		return preview_order(order, catalogue)
	except InvalidOrder as error:
		frappe.throw(str(error), frappe.ValidationError)


@frappe.whitelist(methods=["POST"])
def preview_transcript(pos_profile, transcript):
	catalogue = authorize(pos_profile, hosted=True)
	try:
		return preview_order(provider().extract(transcript), catalogue)
	except (InvalidOrder, ProviderUnavailable) as error:
		frappe.throw(str(error), frappe.ValidationError)


@frappe.whitelist(methods=["POST"])
def transcribe(pos_profile):
	authorize(pos_profile, hosted=True)
	upload = frappe.request.files.get("audio")
	if upload is None:
		frappe.throw("Upload a PCM WAV recording in the audio field", frappe.ValidationError)
	try:
		transcript = provider().transcribe(upload.stream.read(4 * 1024 * 1024 + 1))
		return {"transcript": transcript, "requires_review": True}
	except (InvalidOrder, ProviderUnavailable) as error:
		frappe.throw(str(error), frappe.ValidationError)
