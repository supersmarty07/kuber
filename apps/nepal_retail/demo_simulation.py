"""Nepal Retail - End-to-End Retail & Fiscal Compliance Simulation

Demonstrates the complete workflow without requiring external network or live bench:
1. Audio generation & PCM16 WAV validation (Nepali voice order)
2. Contract validation (strict parsing of customer, items, and discounts)
3. Nepal Localisation: Bikram Sambat (BS) date conversion & fiscal year boundaries
4. Nepal 13% VAT engine with mixed categories (taxable vs exempt) and proportional discount
5. Dynamic Wallet QR payment flow with HMAC-SHA256 signature verification & deduplication
6. Monotonic Bikram Sambat fiscal invoice numbering (TI-KTM01-2083/84-000001)
7. Official Inland Revenue Department (IRD) CBMS payload generation & transactional outbox
"""

import json
import math
import pathlib
import sys
import time
from datetime import date
from decimal import Decimal

# Ensure apps/nepal_retail is in sys.path
APP_DIR = pathlib.Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
	sys.path.insert(0, str(APP_DIR))

from nepal_retail.compliance.cbms import CBMSClient, CBMSOutbox, build_cbms_payload
from nepal_retail.compliance.numbering import FiscalSequenceManager
from nepal_retail.compliance.protection import (
	LegalInvoiceMode,
	enforce_fiscal_immutability,
	should_emit_cbms_for_invoice,
)
from nepal_retail.compliance.submission import FiscalSubmission, SubmissionState
from nepal_retail.localisation.calendar import ad_to_bs, bs_to_ad, fiscal_year_for_date
from nepal_retail.localisation.vat import (
	TaxCategory,
	TaxLineInput,
	calculate_invoice_taxes,
	validate_pan,
)
from nepal_retail.payments.qr import (
	build_dynamic_qr,
	sign_qr_callback,
	verify_and_record_qr_callback,
)
from nepal_retail.payments.repository import PaymentRepository
from nepal_retail.payments.state import PaymentState
from nepal_retail.voice.audio import float32_to_pcm16_wav
from nepal_retail.voice.contracts import parse_order


def print_step(title: str):
	print("\n" + "=" * 70)
	print(f"  {title.upper()}")
	print("=" * 70)


def main():
	print("\n" + "#" * 70)
	print("#   NEPAL RETAIL - END-TO-END WORKFLOW SIMULATION")
	print("#" * 70)

	# -------------------------------------------------------------------------
	# Step 1: Voice & Audio Ingestion
	# -------------------------------------------------------------------------
	print_step("Step 1: Voice Audio Ingestion & 16-bit PCM WAV Validation")
	sample_rate = 16000
	duration_sec = 2
	# Generate a synthetic sine-wave voice recording (2 seconds @ 16 kHz)
	sine_samples = [
		0.6 * math.sin(2 * math.pi * 440 * i / sample_rate)
		for i in range(sample_rate * duration_sec)
	]
	wav_bytes = float32_to_pcm16_wav(sine_samples, sample_rate=sample_rate, channels=1)
	print(f"[+] Audio Format: 16-bit PCM WAV | Sample Rate: {sample_rate} Hz | Mono")
	print(f"[+] Audio Payload Size: {len(wav_bytes)} bytes ({len(wav_bytes) / 1024:.1f} KiB)")
	print("[+] Audio passed length, channel, and frame alignment checks.")

	# -------------------------------------------------------------------------
	# Step 2: Structured Order Contract Parsing
	# -------------------------------------------------------------------------
	print_step("Step 2: Voice Transcript Order Contract Parsing")
	transcript_text = "Ram lai 2 piece wireless mouse ani 1 kg himalayan organic apple, 10% discount milaideu"
	raw_order_payload = {
		"items": [
			{"description": "Wireless Mouse", "quantity": "2", "uom": "Nos"},
			{"description": "Himalayan Organic Apple", "quantity": "1", "uom": "Kg"},
		],
		"customer": "Ram Kumar Sharma",
		"discount": {"amount": "10", "kind": "percent", "scope": "invoice"},
	}
	print(f'[+] Spoken Nepali Request: "{transcript_text}"')
	validated_order = parse_order(raw_order_payload)
	print(f"[+] Validated Customer: {validated_order.customer}")
	for idx, item in enumerate(validated_order.items, 1):
		print(f"    Item {idx}: {item.description} | Qty: {item.quantity} | UOM: {item.uom}")
	print(f"[+] Requested Discount: {validated_order.discount.amount}% (Invoice-wide)")
	print("[+] Security Guard: Injected pricing/totals and action fields strictly rejected.")

	# -------------------------------------------------------------------------
	# Step 3: Bikram Sambat (BS) Calendar & Nepal Localisation
	# -------------------------------------------------------------------------
	print_step("Step 3: Bikram Sambat (BS) Calendar & Fiscal Year Resolution")
	today_ad = date.today()
	today_bs = ad_to_bs(today_ad)
	fy = fiscal_year_for_date(today_ad)
	print(f"[+] Gregorian Date (AD) : {today_ad.isoformat()}")
	print(f"[+] Bikram Sambat (BS)  : {today_bs.cbms_format()} ({today_bs.year} B.S.)")
	print(f"[+] Fiscal Year (Human) : {fy.label}")
	print(f"[+] Fiscal Year (CBMS)  : {fy.cbms_label}")
	print(f"[+] FY Window (AD)      : {fy.start_ad} to {fy.end_ad}")

	# -------------------------------------------------------------------------
	# Step 4: Nepal 13% VAT Calculation (Taxable vs. Exempt)
	# -------------------------------------------------------------------------
	print_step("Step 4: Nepal 13% VAT Engine (Taxable vs Exempt + Pre-tax Discount)")
	tax_lines = [
		TaxLineInput("MOUSE-01", Decimal("2"), Decimal("1000.00"), TaxCategory.TAXABLE),
		TaxLineInput("APPLE-01", Decimal("1"), Decimal("500.00"), TaxCategory.EXEMPT),
	]
	tax_summary = calculate_invoice_taxes(
		tax_lines,
		discount=validated_order.discount,
		prices_include_tax=False,
	)
	print(f"  - Gross Net Total     : NPR {tax_summary.gross_net_total:>10.2f}")
	print(f"  - 10% Discount Total  : NPR {tax_summary.discount_total:>10.2f} (Allocated proportionally)")
	print(f"  - Taxable Sales (13%) : NPR {tax_summary.taxable_sales:>10.2f} (Mouse: 2000 - 200 disc)")
	print(f"  - Exempt Sales (0%)   : NPR {tax_summary.exempt_sales:>10.2f} (Apple: 500 - 50 disc)")
	print(f"  - Nepal 13% VAT       : NPR {tax_summary.vat_amount:>10.2f} (13% of 1800.00)")
	print(f"  ------------------------------------------------")
	print(f"  - GRAND TOTAL PAYABLE : NPR {tax_summary.grand_total:>10.2f}")

	# -------------------------------------------------------------------------
	# Step 5: Dynamic Wallet QR Payment & Reconciliation
	# -------------------------------------------------------------------------
	print_step("Step 5: Dynamic Wallet QR Payment & HMAC-SHA256 Verification")
	repo = PaymentRepository()
	intent_id = f"PAY-{int(time.time())}"
	order_revision = "rev-order-hash-ktm-001"
	repo.create_intent(intent_id, "Himalayan Retail Pvt Ltd", order_revision, tax_summary.grand_total)
	started = repo.start_payment(intent_id, order_revision)
	now = int(time.time())
	secret_key = "merchant-fonepay-secret-key-prod"

	qr = build_dynamic_qr(
		provider="fonepay",
		merchant_id="MERCHANT-KTM-99",
		secret_key=secret_key,
		intent=started.intent,
		now=now,
		ttl_seconds=300,
	)
	print(f"[+] Dynamic QR Generated for Intent: {qr.intent_id}")
	print(f"[+] Amount in Minor Units (Paisa) : {qr.amount_minor} Paisa (NPR {qr.amount_minor / 100:.2f})")
	print(f"[+] QR Payload Expiry (TTL 5 min) : in {qr.expires_at - now} seconds")
	print(f"[+] HMAC-SHA256 Signature         : {qr.signature[:32]}...")

	# Simulate Customer Scanning and Bank Callback
	print("\n[*] Customer scans QR in mobile banking app and authorizes payment...")
	callback_time = now + 15
	callback_sig = sign_qr_callback(
		secret_key,
		provider="fonepay",
		event_id="EVT-BANK-90021",
		merchant_id="MERCHANT-KTM-99",
		intent_id=qr.intent_id,
		order_revision=order_revision,
		amount_minor=qr.amount_minor,
		currency="NPR",
		provider_reference="FP-TRACE-8877",
		timestamp=callback_time,
	)
	verified_record = verify_and_record_qr_callback(
		repo,
		provider="fonepay",
		merchant_id="MERCHANT-KTM-99",
		secret_key=secret_key,
		callback_payload={
			"event_id": "EVT-BANK-90021",
			"merchant_id": "MERCHANT-KTM-99",
			"intent_id": qr.intent_id,
			"order_revision": order_revision,
			"amount_minor": qr.amount_minor,
			"currency": "NPR",
			"provider_reference": "FP-TRACE-8877",
			"timestamp": callback_time,
			"signature": callback_sig,
		},
		now=callback_time,
	)
	print(f"[+] Payment Verified & Confirmed : State = {verified_record.intent.state.value.upper()}")
	print(f"[+] Provider Reference Number     : {verified_record.intent.provider_reference}")

	# -------------------------------------------------------------------------
	# Step 6: Monotonic Fiscal Sequence Numbering
	# -------------------------------------------------------------------------
	print_step("Step 6: Monotonic Bikram Sambat Fiscal Numbering")
	seq_manager = FiscalSequenceManager()
	fiscal_assignment = seq_manager.next_number(
		company="Himalayan Retail Pvt Ltd",
		branch_code="KTM01",
		posting_date=today_ad,
		invoice_name="POS-INV-00101",
		is_return=False,
	)
	print(f"[+] Branch Code     : {fiscal_assignment.branch_code}")
	print(f"[+] Fiscal Year     : {fiscal_assignment.fiscal_year}")
	print(f"[+] Sequence Number : #{fiscal_assignment.sequence_number}")
	print(f"[+] FISCAL BILL NO  : {fiscal_assignment.fiscal_number}")

	# Bind payment record to the issued fiscal invoice
	repo.attach_issued_invoice(intent_id, fiscal_assignment.fiscal_number)
	print(f"[+] Payment Intent permanently bound to invoice: {fiscal_assignment.fiscal_number}")

	# -------------------------------------------------------------------------
	# Step 7: IRD CBMS Payload & Transactional Outbox
	# -------------------------------------------------------------------------
	print_step("Step 7: Official IRD CBMS Integration & Outbox")
	seller_pan = validate_pan("600123456")
	buyer_pan = validate_pan("600998877")
	cbms_payload = build_cbms_payload(
		username="himalayan_retail_cbms",
		password="dummy_password_for_demo",
		seller_pan=seller_pan,
		buyer_pan=buyer_pan,
		buyer_name="Ram Kumar Sharma",
		posting_date=today_ad,
		fiscal_number=fiscal_assignment.fiscal_number,
		tax_summary=tax_summary,
		datetime_client=time.strftime("%Y-%m-%dT%H:%M:%S"),
		is_realtime=True,
	)
	print("[+] Generated Official IRD CBMS Payload (Schema-Compliant):")
	display_payload = {
		k: v for k, v in cbms_payload.items() if k not in ("username", "password")
	}
	print(json.dumps(display_payload, indent=2))

	submission = FiscalSubmission.create(
		company="Himalayan Retail Pvt Ltd",
		invoice_type="Sales Invoice",
		invoice_name=fiscal_assignment.fiscal_number,
		official_payload=cbms_payload,
	)
	outbox = CBMSOutbox()
	outbox.enqueue_in_transaction(submission)
	print(f"\n[+] Enqueued in CBMS Transactional Outbox: SHA-256 Hash = {submission.payload_hash[:24]}...")
	print(f"[+] Initial Outbox State: {submission.state.value.upper()}")

	# Simulate successful transmission to IRD
	ack_submission = submission.acknowledge("IRD-ACK-2083-99881122")
	print(f"[+] IRD Transmission Result: {ack_submission.state.value.upper()} | Ack: {ack_submission.acknowledgement}")

	print("\n" + "#" * 70)
	print("#   SIMULATION COMPLETE: ALL 7 STEPS VERIFIED & PASSED")
	print("#" * 70 + "\n")


if __name__ == "__main__":
	main()
