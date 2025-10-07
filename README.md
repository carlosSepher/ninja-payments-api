# ninja-payments-api (Koban RC)

FastAPI backend that brokers payments against Transbank Webpay Plus, Stripe Checkout and PayPal Checkout while persisting every event in PostgreSQL. This release candidate is code-named **Koban**.

## Highlights
- Unified `/api/payments` surface to create, commit, refund and list transactions across providers.
- PostgreSQL `payments` schema stores orders, attempts, status history, webhooks, refunds and provider IO.
- Bearer token API auth plus per-company credentials; HTTP Basic protects the interactive docs.
- Provider adapters log verbose JSON for create/commit/refund calls to aid reconciliation.
- Operational endpoints expose health/metrics for uptime dashboards.

## Stack
- Python 3.11, FastAPI, Pydantic v2, Uvicorn.
- psycopg2 with raw SQL repositories; connection pool in `app/db/client.py`.
- Transbank REST + SDK, Stripe (`stripe` SDK), PayPal Checkout (HTTP + signature verification).
- pytest for automated tests.
- Dockerfile (uvicorn) and `docker-compose.yml` for local Postgres.

## Quickstart
### Local development
```bash
python -m venv .venv
source .venv/bin/activate || .\.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
cp -n .env.example .env 2>/dev/null || copy .env.example .env
```

### Database
- Default setup expects PostgreSQL with the schema in `db/schema.sql`.
- For local testing run `docker compose up -d postgres`; the schema is applied on first boot.

### Run the API
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Verify it:
```bash
curl http://localhost:8000/health
```
Docs are available at `/docs` and `/redoc` (they prompt for HTTP Basic credentials).

### Tests
```bash
pytest
```

## Configuration
Edit `.env` (or environment variables) before starting the service. Essential keys:

| Variable | Purpose |
| --- | --- |
| `API_BEARER_TOKEN` | Token required in `Authorization: Bearer` header for all `/api/payments` endpoints |
| `API_BASIC_USERNAME` / `API_BASIC_PASSWORD` | Credentials to reach `/docs`, `/redoc`, `/openapi.json` |
| `TBK_HOST`, `TBK_API_BASE`, `TBK_API_KEY_ID`, `TBK_API_KEY_SECRET` | Transbank REST integration (integration environment by default) |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Stripe Checkout session + webhook verification |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` | PayPal Checkout + webhook signature verification |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_SCHEMA` | PostgreSQL connectivity |

Other useful flags:
- `LOG_PROVIDER_EVENTS=true` (default) persists provider request/response payloads in `payments.provider_event_log`.
- `APP_ENV`, `APP_VERSION`, `BASE_URL` enrich `/health/metrics` output.

## API Overview
All business endpoints live under `/api/payments` and require:
- `Authorization: Bearer <API_BEARER_TOKEN>`
- `Content-Type: application/json`
- `company_id` + `company_token` fields (validated against the `company` table)
- Optional `Idempotency-Key` header for safe retries.

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/api/payments` | Bearer + company body | Create a payment attempt and receive `{status, redirect}` (Webpay auto-post form or Stripe/PayPal URL) |
| `GET` | `/api/payments` | Bearer | Filter payments by provider, status, date range (limit 1-500) |
| `GET` | `/api/payments/pending` | Bearer | Last 200 pending attempts |
| `GET` | `/api/payments/all` | Bearer | Latest 200 attempts for quick diagnostics |
| `GET/POST` | `/api/payments/tbk/return` | none (PSP) | Handles Webpay return, PayPal cancel and generic status polling; responds with JSON or redirects to stored success/failure/cancel URLs |
| `GET` | `/api/payments/redirect` | Bearer | Rebuild redirect info (`token_ws`, URL, method) for a stored attempt |
| `POST` | `/api/payments/refund` | Bearer + company body | Trigger PSP refund (amount optional; defaults to full refund for Webpay) |
| `POST` | `/api/payments/stripe/webhook` | Stripe signature | Stripe webhook endpoint (verifies signature, keeps status/refunds/disputes in sync) |
| `POST` | `/api/payments/paypal/webhook` | PayPal verification | PayPal webhook endpoint (verifies signature, captures orders, marks refunds/disputes) |
| `GET` | `/health` | none | Liveness probe |
| `GET` | `/health/metrics` | none | Connectivity + status summary (counts, pending backlog, last-24h volume) |

### Create payment payload
```json
{
  "buy_order": "o-123",
  "amount": 1000,
  "currency": "CLP",
  "payment_type": "credito",
  "commerce_id": "store-001",
  "product_id": "sku-123",
  "product_name": "Plan Ninja",
  "company_id": 1,
  "company_token": "company-token",
  "return_url": "http://localhost:8000/api/payments/tbk/return",
  "success_url": "http://localhost:3000/checkout/success",
  "failure_url": "http://localhost:3000/checkout/failure",
  "cancel_url": "http://localhost:3000/checkout/canceled",
  "provider": "webpay",
  "customer_rut": "12.345.678-9"
}
```
- Webpay requires `currency=CLP`.
- Stripe/PayPal accept decimal currencies (`amount` expressed in minor units).
- Response includes `redirect {url, token, method, form_fields}`, `internal_id` and `provider_transaction_id`.

### Return & redirect behaviour
- Webpay returns `token_ws` (authorized/failed) or `TBK_TOKEN` (cancel). Service commits via Transbank SDK and redirects/returns JSON.
- PayPal approval uses the same return endpoint; cancellation expects `?paypal_cancel=1&token=<order_id>`.
- Stripe frontends rely on `success_url`/`cancel_url`; webhook ensures final status (optional manual `/tbk/return?token=cs_test...&format=json`).

## Data Model
Schema lives in `db/schema.sql` (detailed walkthrough in `readme-db.md`). Cornerstones:
- `payment_order` stores the business order (unique `buy_order` per company) and expected amount/currency.
- `payment` keeps each attempt with provider metadata, URLs, status, and optional idempotency key.
- `payment_state_history` is trigger-managed for every status transition.
- `provider_event_log`, `webhook_inbox`, `refund`, `dispute`, `settlement_*`, `status_check` capture IO, callbacks and downstream workflows.

## Observability & Logging
- JSON logging configured in `app/logging.py` (structured fields for traceability).
- Provider adapters persist outgoing/incoming payloads when `LOG_PROVIDER_EVENTS=true`.
- `/health/metrics` reports DB connectivity, per-status counts, pending by provider and last 24h volume.
- Stripe webhooks: `stripe listen --events checkout.session.completed,payment_intent.payment_failed --forward-to http://localhost:8000/api/payments/stripe/webhook`.
- PayPal webhooks: configure Sandbox webhook to hit `/api/payments/paypal/webhook`; signature verification uses `PAYPAL_WEBHOOK_ID`.

## Integration Resources
- Frontend integration guide: `docs/INTEGRATION_GUIDE.md`.
- Postman collection: `ninja-payments-api.postman_collection.json` (covers Webpay, Stripe, PayPal, refunds, returns).
- Database reference: `readme-db.md`.

## Troubleshooting Quick Notes
- **Stripe**: ensure `success_url` carries `?session_id={CHECKOUT_SESSION_ID}`; keep webhook listener running and set `STRIPE_WEBHOOK_SECRET`.
- **PayPal**: Sandbox requires `currency=USD`; provide valid client credentials and webhook id, pass `paypal_cancel=1` on cancel flow.
- **Webpay**: 401 responses usually mean wrong Basic credentials when creating the payment (`API_BASIC_USERNAME`/`API_BASIC_PASSWORD`).

---
This README reflects the Koban release candidate. Update documentation alongside code changes to keep the RC accurate.
