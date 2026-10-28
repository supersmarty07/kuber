# Implementation Roadmap

## Current Milestone

Backend contracts and reviewed voice preview are implemented. Unit tests use
mocked Frappe/HTTP and do not require a live site. Payment and CBMS models are
foundations only. No invoice, tax or deployment configuration has been changed.

## Compatibility Gate

1. Select the maintained POS Awesome upstream and verify licensing, supported
   stable Frappe/ERPNext releases and invoice document flow. Pin commits, runtimes,
   database and frontend toolchain after staging smoke tests.
2. Verify native catalogue API compatibility and run real permission tests for
   assigned users, company/warehouse restrictions and item groups on that release.
3. Decide one legal invoice per sale. ERPNext POS receipt consolidation must not
   duplicate CBMS submissions, VAT, stock or ledger postings. Confirm this decision
   against current IRD requirements before implementing fiscal hooks.

## Localisation and Deployment

1. Configure separate staging/production, TLS, restricted database and Redis,
   protected secrets, monitoring, encrypted offsite backups and restore drills.
2. Start around 2-4 vCPU and 8 GB RAM, then load-test peak counters plus backups.
   Use hosted AI initially; do not assume local LLM inference fits that VPS.
3. Add reviewed NPR, Asia/Kathmandu, PAN/VAT and POS setup migrations. Configure
   13% only for applicable taxable items; test exempt/zero-rated items, discount
   ordering, rounding and inclusive/exclusive pricing.
4. Choose a maintained BS calendar library and validate fiscal boundaries rather
   than assuming fixed Gregorian July dates. Test scanners, UOM, batches/serials,
   repeated scans and receipt printers before adding scanner-specific code.

## Voice-to-Draft Pilot

1. Validate hosted models with consenting local speakers and noisy shop recordings;
   measure item/quantity/discount extraction accuracy, latency and cost.
2. Add the POS Awesome microphone and review interface using its supported cart
   adapter. Implement tested recording format conversion or bounded WebM support.
3. Resolve customer, item and UOM ambiguity. Enforce server-side pricing, taxes,
   discount permissions and manager approval. Keep manual checkout confirmation.
4. Test desktop/touch flows, denied microphone permission, interrupted recording,
   malformed provider output and provider failure. Never auto-submit from speech.

## Payments (Parallel After Foundation)

1. Obtain one wallet merchant API contract and one terminal ECR/SDK protocol.
   A bank name alone does not establish integration support.
2. Add persistent intents/events, unique constraints, row locking, provider binding,
   immutable amounts/revisions and durable callback deduplication.
3. Implement verified wallet QR first. Add the outbound-connected local terminal
   agent only where required, with device registration, revocation and command TTL.
4. Test duplicate/out-of-order callbacks, unknown outcomes, terminal restart, amount
   mismatches and payment success followed by invoice failure. Do not recharge to
   repair an invoice failure. Reconcile settlements and authorized refunds.

## Fiscal Protection and CBMS (Parallel After Legal Gate)

1. Obtain current official IRD rules, schemas, authentication, sandbox and required
   registration/audit artifacts. Confirm outage behavior; do not promise approval
   dates or certification based on a functioning HTTP integration.
2. Add scoped invoice-field protection and approved credit/debit/return flows for
   every legal invoice path. Protect delete, cancel and rename operations without
   blocking necessary accounting/status operations indiscriminately.
3. Implement transactional company/branch/fiscal-year sequences with uniqueness,
   concurrency tests, rollover and approved gap/void handling. Offline fiscal
   issuance remains excluded until explicitly approved.
4. Persist the fiscal snapshot and CBMS outbox in the legal invoice transaction;
   dispatch after commit and recover missed enqueues with scheduled reconciliation.
5. Implement the official payload adapter, TLS, bounded retries where safe,
   acknowledgements, unknown-outcome inquiry and rejected-submission operations.
6. Validate duplicate POS/consolidated reporting prevention, rollback behavior,
   worker crashes, sandbox acknowledgements and applicable production approval.

## Rollout Gate

Pilot one counter first. Reconcile sales, tax, inventory, ledger and settlements;
exercise outages and restore/rollback procedures. Agree retention and recovery
objectives. Expand only with required provider authorizations and IRD approval.

Open-source licensing does not remove hosting, AI usage, merchant fees, maintenance
or legal compliance costs. Audit third-party localisation repositories before use.