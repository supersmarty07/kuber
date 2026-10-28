# Nepal Retail

An independently packaged Frappe extension inside this workspace. ERPNext core is
unchanged. This is a backend foundation, not a finished POS or certified Nepal
billing product. POS Awesome is not installed or verified compatible yet.

## Implemented

- Strict structured order validation with exact decimal quantities and requested
  discounts. Model-supplied prices, totals, item identifiers and actions are rejected.
- Read-only preview API using ERPNext's POS item search with additional profile,
  assigned-user, company, warehouse and Item permission checks.
- Optional OpenAI Whisper transcription and schema-constrained extraction. Audio
  must be 16-bit PCM WAV, mono/stereo, 8-48 kHz, at most 30 seconds and 4 MiB.
- Server-only provider settings, disabled-by-default feature flags, ten requests
  per user per calendar minute, HTTP timeouts, response size bounds and no redirects.
- Immutable payment state model with exact NPR minor units, changed-cart detection,
  duplicate-result handling and safeguards against recharging unknown payments.
- Immutable fiscal payload envelope and explicit unknown-outcome retry gate.

Every order preview requires review. It does not select a customer or item, apply
discounts, save/submit an invoice, change stock, charge money or send data to IRD.
Catalogue candidates contain no trusted checkout totals: ERPNext must recalculate
pricing/tax and validate selections when the future cart adapter is implemented.

## Local Verification

From the workspace root:

```bash
python -m pip install -e ./apps/nepal_retail "ruff>=0.9,<1"
PYTHONPATH=apps/nepal_retail python -m unittest discover -s apps/nepal_retail/tests -v
ruff check apps/nepal_retail
ruff format --check apps/nepal_retail
python -m pip wheel --no-deps ./apps/nepal_retail --wheel-dir /tmp/nepal-retail-wheels
```

The tests mock Frappe and external HTTP. They do not prove live site permissions,
Redis behavior, database transactions, provider accuracy, or POS compatibility.
The app's Python floor is 3.10; the selected Frappe/ERPNext release may require a
newer runtime. This checkout requires Python 3.14 and is a development branch.

## Staging Installation

First verify and pin a mutually compatible stable Frappe, ERPNext and POS Awesome
stack in a separate bench. Do not downgrade this working tree in place. In that
bench, the intended local-app installation is:

```bash
bench get-app --soft-link /workspaces/kuber/apps/nepal_retail
bench --site retail.test install-app nepal_retail
bench --site retail.test migrate
```

These commands have not been run against a live bench in this workspace. The app
does not declare a supported Frappe version until that compatibility test passes.

Site configuration flags must be JSON booleans. Leave both absent/false initially:

| Site configuration key | Purpose |
| --- | --- |
| `nepal_retail_preview_enabled` | Enables reviewed structured-order previews |
| `nepal_retail_hosted_voice_enabled` | Allows transcripts/audio to leave the server for OpenAI |
| `nepal_retail_openai_api_key` | Server-side secret, never browser configuration |
| `nepal_retail_extraction_model` | Explicit model supporting Chat Completions structured outputs |

Manage secrets through protected deployment/site configuration, never Git or
browser code. Set provider budget limits and obtain consent before enabling hosted
processing. Provider availability, costs and language accuracy require validation.
The app creates no audio File records and does not intentionally log transcripts;
review reverse-proxy, request logging, temporary upload and provider retention too.
Configure a request-body limit at the proxy/site boundary as well as the app limit.
The fixed-window limiter allows bursts across a minute boundary, not a rolling
ten-request guarantee. Redis must be available; failure must not bypass the limit.

## API Contract

All three endpoints are POST-only, require a logged-in user or authenticated API
identity with POS Invoice create access and access to the named POS Profile.
Use Frappe's normal authenticated session/CSRF or token authentication.

- `nepal_retail.api.voice.preview`: JSON `pos_profile` and structured `order` object.
- `nepal_retail.api.voice.transcribe`: multipart `pos_profile` and WAV file `audio`.
  Returns a transcript for review; does not automatically extract or sell.
- `nepal_retail.api.voice.preview_transcript`: JSON `pos_profile` and reviewed
  `transcript`; returns a catalogue candidate preview.

Example body for the first endpoint:

```json
{
  "pos_profile": "Your POS Profile",
  "order": {
    "items": [{"description": "wireless mouse", "quantity": "1", "uom": null}],
    "customer": "Ram",
    "discount": {"amount": "500", "kind": "amount", "scope": "unspecified"}
  }
}
```

The eventual POS Awesome adapter must display transcript/candidate review, resolve
customer/item/UOM ambiguity, enforce discount authorization and use the pinned
POS cart's supported API. No generic `frappe.model.set_value` assumption is made.
The current transcription endpoint requires WAV, not MediaRecorder WebM/Opus.

## Payment and Fiscal Boundaries

Payment and fiscal classes are **in-memory domain models only**. They have no
DocTypes, persistence, transaction locks, global uniqueness, queues or live adapters.
They cannot enforce financial idempotency across processes by themselves.
`record_verified_success` is an internal contract for a future authenticated
provider adapter, not proof of verification and not a public callback endpoint.
Similarly, a payload hash detects changes but does not provide legal immutability
or protection against a malicious administrator. Test payload fields are fixtures,
not an IRD schema.

Production requires verified merchant/provider events, intent-bound merchant and
order checks, durable deduplication, atomic finalization, settlement reconciliation,
and support for paid-but-unissued invoices. The store agent must connect outbound
and use a bank-authorized terminal protocol without accessing PAN/PIN/CVV.

CBMS still requires the current official schema, credentials, legal-document
decision, transactional outbox, after-commit worker, recovery/reconciliation,
fiscal numbering, invoice protection, approved correction flows and applicable
IRD approval. No compliance hooks are activated by this package.

See [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) for remaining work.