# ninja-payments-api

FastAPI service exposing a minimal payments API using Transbank Webpay Plus, persisting all transactions in PostgreSQL (raw SQL via psycopg2).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate || .\.venv\Scripts\activate
pip install -r requirements.txt
cp -n .env.example .env 2>/dev/null || copy .env.example .env
```

## Cómo correrlo rápido

1. Duplica `.env.example` a `.env` y completa tus credenciales (puedes usar Neon o levantar la base local).
2. (Opcional) Levanta Postgres con los seed incluidos: `docker compose up -d postgres`.
3. Inicia la API con recarga en caliente: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`.
4. Comprueba que responde: `curl http://localhost:8000/health` o abre <http://localhost:8000/docs>.

## Run locally

1. Configura `.env` con tus credenciales (ejemplo Neon):
   ```env
   API_BEARER_TOKEN=testtoken
   DB_HOST=ep-nameless-wind-acz07abp-pooler.sa-east-1.aws.neon.tech
   DB_PORT=5432
   DB_USER=neondb_owner
   DB_PASSWORD=********
   DB_NAME=neondb
   DB_SCHEMA=payments
   ```
2. (Opcional) Levanta Postgres local con los datos seed del proyecto:
   ```bash
   docker compose up -d postgres
   ```
3. Ejecuta la API con recarga en caliente:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
4. Verifica el estado:
   ```bash
   curl http://localhost:8000/health
   ```

## Usage

Create a payment:

```bash
curl -X POST http://localhost:8000/api/payments \
  -H 'Authorization: Bearer testtoken' \
  -H 'Content-Type: application/json' \
  -d '{"buy_order":"o-123","amount":1000,"currency":"CLP","payment_type":"credito","commerce_id":"store-001","product_id":"sku-123","product_name":"Plan Ninja","company_id":1,"company_token":"company-token","return_url":"http://localhost:8000/api/payments/tbk/return","success_url":"http://localhost:3000/checkout/success","failure_url":"http://localhost:3000/checkout/failure","cancel_url":"http://localhost:3000/checkout/canceled"}'
```

The response contains a token and redirect URL. A minimal auto-post form looks like:

```html
<form id="pay" action="https://webpay.example/" method="POST">
  <input type="hidden" name="token_ws" value="TOKEN" />
</form>
<script>document.getElementById('pay').submit();</script>
```

Transbank will call `/api/payments/tbk/return` with `token_ws` (authorized/failed) or
`TBK_TOKEN` when the user cancels. If you provide `success_url`, `failure_url`,
or `cancel_url`, the API will redirect the browser (303) to those URLs appending
`status` and `buy_order` as query parameters. Otherwise, the API returns a JSON
with the final status.

## API documentation

- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>
- OpenAPI JSON: <http://localhost:8000/openapi.json>

FastAPI expone estas rutas automáticamente a partir de los routers definidos en el proyecto, por lo que no se requiere configuración adicional para visualizarlas.

## Tests

```bash
pytest
```

## Overview

This service exposes a small, opinionated API to create payment transactions against Transbank Webpay Plus (integration environment), handle the browser return from Webpay, and report the final status back to the client. It favors simplicity and clear flows suitable for demos, PoCs, and local development.

Key traits:
- Minimal endpoints: create payment, handle return/webhooks, refunds and admin lookups.
- Real calls to Transbank, Stripe and PayPal using providers per PSP.
- Optional frontend redirects after return (success/failure/cancel).
- PostgreSQL is the source of truth; every provider interaction and state transition is persisted.
- Bearer token authentication for the create/list/refund endpoints.
- JSON logging plus structured provider-call logging for troubleshooting.

## Architecture

```mermaid
flowchart TD
  A[Frontend]
  B[ninja-payments-api]
  C[PSP REST Webpay/Stripe/PayPal]
  D[PSP UI]
  E[Return URL API]
  F[PSP SDK/Commit]

  A -->|POST /api/payments| B
  B -->|Create REST| C
  C -->|url, token| B
  B -->|url, token, method| A
  A -->|Redirect POST/GET| D
  D -->|Redirect back| E
  E -->|Commit if needed| F
  F -->|Result| E
  E -->|303 to Front| A
```

Components:
- App: `app/main.py` wires routers and logging.
- Routes: `app/routes/health.py`, `app/routes/payments.py`.
- Service: `app/services/payments_service.py` contains business rules and orchestrates provider/store operations.
- Providers: `app/providers/` implement Webpay, Stripe and PayPal (create/commit/status/refund) with detailed IO logging.
- Store (DB): `app/repositories/pg_store.py` persists transactions, provider events, refunds and webhooks in PostgreSQL.
- Config: `app/config.py` (Pydantic v2 settings; extra env vars are ignored). Toggle `LOG_PROVIDER_EVENTS` to disable provider-call persistence when needed.

## Database Usage

Schema lives under the `payments` schema (see `db/schema.sql`). Key tables:
- `payment_order`: one business order per `buy_order` (UNIQUE). Upserted on create and reused by every attempt (`payment`). Tracks expected amount/currency and overall order status.
- `payment`: one row per payment attempt. Contains PSP metadata (`provider`, `token`, redirect URLs, idempotency_key). Updated on commit/cancel/refund to reflect the current status.
- `payment_state_history`: trigger-driven append-only audit trail for every status transition (created/commit/failed/cancel/refund).
- `provider_event_log`: detailed record of every outbound call to a PSP (request/response headers, bodies, latency, errors). Filled automatically by the provider classes when `LOG_PROVIDER_EVENTS=true`.
- `webhook_inbox`: verified webhook payloads (Stripe, PayPal). Keeps headers, payload, verification result and the related payment for replay/debugging.
- `refund`: every refund attempt we perform is recorded here with amount, PSP refund id, raw payload and status (`REQUESTED`/`SUCCEEDED`/`FAILED`). Populated directly by `PaymentsService.refund`.
- `service_runtime_log`: runtime events for the API itself (startup, health heartbeats, future uptime pings).
- `status_check`: reserved for external reconciliers to queue background checks.
- `dispute`: placeholder for post-clearing disputes/chargebacks.
- `settlement_batch` / `settlement_item`: optional ingestion for PSP settlement files linked back to payments.
- `provider_account`: metadata for PSP accounts/environments (commerce codes, etc.).

How the API uses the DB (high level):
- Create (POST `/api/payments`): upsert `payment_order`; insert `payment` (PENDING); store redirect/token. Provider request/response is written to `provider_event_log`.
- Return/commit (GET/POST `/api/payments/tbk/return`, Stripe/PayPal webhooks): call provider `commit`, update `payment` + `payment_state_history`, log the provider response and, if applicable, capture the webhook payload.
- Cancel (TBK_TOKEN/PayPal cancel flag): mark `payment` as `CANCELED` and append to history.
- Refund (POST `/api/payments/refund`): call provider refund, persist the attempt in `refund`, log the provider interaction and update `payment` to `REFUNDED` when successful.
- Observability: optional heartbeat routines can insert into `service_runtime_log`; a future reconciler can leverage `status_check`.

Code map highlights:
- App boot/CORS: `app/main.py`
- Payment routes: `app/routes/payments.py`
- Service orchestration: `app/services/payments_service.py`
- Providers: `app/providers/transbank_webpay_plus.py`, `app/providers/stripe_checkout.py`, `app/providers/paypal_checkout.py`
- Store helpers (payments, refunds, provider logs): `app/repositories/pg_store.py`

## Observability & Webhooks

- Provider logging is enabled by default (`LOG_PROVIDER_EVENTS=true`). Each call captures request/response data, latency and errors in `payments.provider_event_log`.
- Refund attempts are normalized across PSPs; successful or failed attempts are stored in `payments.refund` with the PSP response payload for auditing.
- Stripe webhooks: run `stripe listen --events checkout.session.completed,payment_intent.payment_failed --forward-to http://localhost:8000/api/payments/stripe/webhook` when developing locally.
- PayPal webhooks: configure the webhook endpoint (`/api/payments/paypal/webhook`) in the PayPal developer dashboard or expose the API via tunnel/EC2 to receive sandbox events.
- `GET /health/metrics` surfaces uptime, DB connectivity, status counts, pending-by-provider breakdown and last-24h volume for dashboards.

## Managed Postgres (Neon) setup

If you prefer a hosted database instead of running Postgres locally, Neon offers a free tier that works well for development.

1. Create a Neon project at <https://neon.tech>, pick a region close to your stack and accept the default `main` branch/database/user.
2. From the **Connection Details** panel copy the primary connection string (it looks like `postgresql://user:password@ep-xxxx.neon.tech/neondb`). Neon requires TLS, so append `?sslmode=require` when using `psql` or application DSNs.
3. Apply the schema once:
   ```bash
   export NEON_URL="postgresql://user:password@ep-xxxx.neon.tech/neondb?sslmode=require"
   psql "$NEON_URL" -f db/schema.sql
   ```
4. Update `.env` so the service points at Neon:
   ```env
   DB_HOST=ep-xxxx.neon.tech
   DB_PORT=5432
   DB_USER=your_user
   DB_PASSWORD=your_password
   DB_NAME=neondb
   DB_SCHEMA=payments
   LOG_PROVIDER_EVENTS=true
   ```
   If you prefer using a single DSN, you can also export `DATABASE_URL=$NEON_URL` and derive the individual fields in `app/config.py`.
5. Free projects suspend when idle; the first query may take a couple of seconds while Neon resumes. For production or long-running tests consider upgrading the project or disabling auto-suspend in Neon settings.

Once the environment variables are in place, restart the API (`uvicorn`/`gunicorn`) and it will use Neon transparently.

## Domain & Statuses

```mermaid
classDiagram
  class Payment {
    +int id
    +str buy_order
    +int amount
    +Currency currency
    +PaymentStatus status
    +PaymentType payment_type
    +str commerce_id
    +str product_id
    +str product_name
    +str? token
    +str? redirect_url
    +datetime? created_at
    +str? success_url
    +str? failure_url
    +str? cancel_url
  }
  class PaymentStatus {
    <<enum>>
    PENDING
    AUTHORIZED
    FAILED
    CANCELED
    REFUNDED
    TO_CONFIRM
    ABANDONED
  }
  class PaymentType {
    <<enum>>
    credito
    debito
    prepago
  }
  Payment --> PaymentStatus
  Payment --> PaymentType
```

State machine:

```mermaid
stateDiagram-v2
  [*] --> PENDING
  PENDING --> AUTHORIZED: commit response_code == 0
  PENDING --> FAILED: commit response_code != 0
  PENDING --> CANCELED: TBK_TOKEN cancel
  PENDING --> TO_CONFIRM: async PSP pending state
  TO_CONFIRM --> AUTHORIZED: reconciled success
  TO_CONFIRM --> FAILED: reconciled failure
  PENDING --> ABANDONED: scheduler timeout
  AUTHORIZED --> REFUNDED: refund
```

## Endpoints

1) POST `/api/payments` (Register/Create Payment)
- Auth: `Authorization: Bearer <token>` (default dev token: `dev-token` in `.env.example`).
- Optional: `Idempotency-Key` header to safely retry the same request.
- Body:
  - `buy_order` (string)
  - `amount` (int, > 0)
  - `currency` (string):
    - `CLP` for Webpay (Transbank)
    - `USD` recommended for PayPal Sandbox
  - `payment_type` (string): `credito`, `debito` or `prepago`
  - `commerce_id` (string): parametric identifier of the commerce/tenant
  - `product_id` (string): SKU or internal product identifier
  - `product_name` (string): product name snapshot at purchase time
  - `company_id` (int) and `company_token` (string): credentials issued for the consuming company
  - `return_url` (string): where Webpay/PSP will redirect after payment
  - `provider` (string, optional): `webpay` (default), `stripe`, or `paypal`. Unsupported providers return 400.
  - `success_url`, `failure_url`, `cancel_url` (strings, optional): override browser landing pages after the provider returns control
  - Optional `Idempotency-Key` header keeps retries idempotent per company.
  - Response includes the redirect payload plus `internal_id` (internal transaction id) and `provider_transaction_id` (PSP reference) for auditing.

Example request:

```bash
curl -X POST http://localhost:8000/api/payments \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "buy_order":"o-123",
    "amount":1000,
    "currency":"CLP",
    "return_url":"http://localhost:8000/api/payments/tbk/return",
    "provider":"webpay",
    "success_url":"http://localhost:3000/checkout/success",
    "failure_url":"http://localhost:3000/checkout/failure",
    "cancel_url":"http://localhost:3000/checkout/canceled"
  }'
```

Response (example):

```json
{
  "status": "PENDING",
  "redirect": {
    "url": "https://webpay3gint.transbank.cl/webpayserver/initTransaction",
    "token": "01ab...",
    "method": "POST",
    "form_fields": { "token_ws": "01ab..." }
  }
}
```

Frontend then renders and auto‑submits a form to `redirect.url` with `token_ws` (Webpay) or navigates via GET (Stripe/PayPal).

Code references:
- Route definition: app/routes/payments.py:36
- Service logic (create + idempotency): app/services/payments_service.py:28
- Provider selection: app/providers/factory.py:19
- Webpay create (REST): app/providers/transbank_webpay_plus.py:31
- Stripe create (Checkout Session): app/providers/stripe_checkout.py:19
- PayPal create (Orders v2): app/providers/paypal_checkout.py:19

2) GET `/api/payments` (Consulta de movimientos)
- Auth: Bearer token.
- Filtros opcionales vía query string:
  - `provider`: `webpay`, `stripe`, `paypal`.
  - `status`: `PENDING`, `AUTHORIZED`, `FAILED`, `CANCELED`, `REFUNDED`, `TO_CONFIRM`, `ABANDONED`.
  - `start_date` y `end_date`: timestamp ISO-8601 (inclusive) para acotar por `created_at`.
  - `limit`: máximo de filas (1-500, default 200).
- Respuesta: lista de `PaymentSummary` con tipo de pago, comercio/producto, identificadores del PSP y fechas.

3) GET/POST `/api/payments/tbk/return` (Transbank Return)
- Webpay redirects the user’s browser here with either:
  - `token_ws` when authorized or failed
  - `TBK_TOKEN` when canceled
- Behavior:
  - If `token_ws` exists: the API performs `commit` against Transbank via SDK to finalize the transaction and determine `AUTHORIZED` or `FAILED`.
  - If `TBK_TOKEN` exists: the API marks the transaction as `CANCELED`.
  - If `success_url`/`failure_url`/`cancel_url` were provided on creation, the API responds with a `303 See Other` redirect to the corresponding URL, appending `status` and `buy_order` as query parameters.
  - Otherwise, it returns a JSON body: `{ "status": "AUTHORIZED|FAILED|CANCELED" }`.

Provider-specific notes:
- Webpay (Transbank): return carries `token_ws` or `TBK_TOKEN` and the API performs commit.
- Stripe Checkout: the browser navigates to Stripe and then back to your `success_url`/`cancel_url` directly; use webhooks to confirm status (optional polling via provider `commit`).
- PayPal Checkout: the browser is redirected to PayPal for approval, then back to the API `return_url` with `token` (order id). The API captures the order (commit). For cancel, set `cancel_url` to the API return endpoint with `?paypal_cancel=1` so the API can mark it as `CANCELED` and redirect to your frontend. In Sandbox, use `USD` currency.

Example redirects:
- `http://localhost:3000/checkout/success?status=AUTHORIZED&buy_order=o-123`
- `http://localhost:3000/checkout/failure?status=FAILED&buy_order=o-123`
- `http://localhost:3000/checkout/canceled?status=CANCELED&buy_order=o-123`

Code references:
- Route definition: app/routes/payments.py:72
- Commit service: app/services/payments_service.py:102
- Webpay commit (SDK): app/providers/transbank_webpay_plus.py:56

4) GET `/health`
- Simple liveness check. Returns `{ "status": "ok" }`.

5) GET `/health/metrics`
- Observability endpoint returning uptime, database connectivity, per-status counters, pending backlog by provider and last-24h volume. Ideal for dashboards.

6) POST `/api/payments/stripe/webhook`
- Receives Stripe events, verifies the signature, and updates the payment state based on the Checkout Session id.
- Use for production-grade confirmation of Stripe payments.
- Local dev: `stripe listen --forward-to http://localhost:8000/api/payments/stripe/webhook` and set `STRIPE_WEBHOOK_SECRET`.

7) GET `/api/payments/pending`
- Devuelve las transacciones `PENDING` más recientes (límite 200).
- Requiere Bearer token.
8) POST `/api/payments/refund`
- Auth: Bearer
- Body: `{ "token": "...", "amount": <int|null>, "company_id": <int>, "company_token": "..." }`
- Issues a refund with the provider associated to the token.
  - Stripe: refunds the PaymentIntent (amount in minor units for decimal currencies; zero-decimal for CLP). If omitted, full refund.
  - PayPal: refunds the latest capture of the Order (amount in major units; Sandbox typically USD). If omitted, full refund.
  - Webpay: refunds/nullifications via REST (amount in CLP). If omitted, defaults to full refund of the original amount.
- Response: `{ "status": "REFUNDED" | current_status }` (the in-memory store is updated to `REFUNDED` on success).

Example request (JSON):

```json
{ "token": "01ab...", "amount": 1000, "company_id": 1, "company_token": "company-token" }
```

Example response (JSON):

```json
{ "status": "REFUNDED" }
```

Code references:
- Route definition: app/routes/payments.py:263
- Service refund logic (defaults full for TBK): app/services/payments_service.py:173
- Webpay refund (REST `/transactions/{token}/refunds`): app/providers/transbank_webpay_plus.py:106
- Stripe refund: app/providers/stripe_checkout.py:110
- PayPal refund: app/providers/paypal_checkout.py:138

9) GET `/api/payments/redirect`
- Auth: Bearer
- Query: `?token=...`
- Returns the redirect information to resume a pending checkout flow:
  - Webpay: `{ url, token, method: POST, form_fields: { token_ws } }`
  - Stripe/PayPal: `{ url, token, method: GET }`

Code references:
- Route definition: app/routes/payments.py:248

8) POST `/api/payments/paypal/webhook`
- Verifies signature using PayPal Verify Webhook Signature API.
- On `CHECKOUT.ORDER.APPROVED`, captures the order (commit) and updates status to `AUTHORIZED`.
- On `PAYMENT.CAPTURE.REFUNDED` / `PARTIALLY_REFUNDED`, if `order_id` is present in related ids, marks the transaction as `REFUNDED` in-memory.

Code references:
- Route definition: app/routes/payments.py:219

### Sequence Diagrams

#### Webpay (Transbank)

```mermaid
sequenceDiagram
  participant F as Frontend Browser
  participant API as ninja-payments-api
  participant TBK as Webpay Transbank

  F->>API: POST /api/payments provider=webpay
  API->>TBK: Create transaction REST
  TBK-->>API: {url, token}
  API-->>F: {status:PENDING, redirect:{url, token, method:POST}}
  F->>TBK: POST redirect.url token_ws
  TBK-->>F: 302 to return_url with token_ws
  F->>API: GET/POST /api/payments/tbk/return?token_ws=...
  API->>TBK: commit token_ws via SDK
  TBK-->>API: response_code
  alt Authorized
    API-->>F: 303 to success_url?status=AUTHORIZED&buy_order=...
  else Failed
    API-->>F: 303 to failure_url?status=FAILED&buy_order=...
  end
  opt User cancels
    TBK-->>F: 302 to return_url with TBK_TOKEN
    F->>API: GET/POST /api/payments/tbk/return?TBK_TOKEN=...
    API-->>F: 303 to cancel_url?status=CANCELED&buy_order=...
  end
```

#### PayPal (Checkout Orders v2)

```mermaid
sequenceDiagram
  participant F as Frontend Browser
  participant API as ninja-payments-api
  participant PP as PayPal

  F->>API: POST /api/payments provider=paypal, currency=USD
  API->>PP: POST /v1/oauth2/token
  PP-->>API: access_token
  API->>PP: POST /v2/checkout/orders intent=CAPTURE
  PP-->>API: {approve_url, order_id}
  API-->>F: {status:PENDING, redirect:{url:approve_url, method:GET}}
  F->>PP: GET approve_url user approves
  PP-->>F: 302 to return_url with token=order_id
  F->>API: GET /api/payments/tbk/return?token=order_id
  API->>PP: POST /v2/checkout/orders/{order_id}/capture
  PP-->>API: status=COMPLETED or other
  alt Completed
    API-->>F: 303 to success_url?status=AUTHORIZED&buy_order=...
  else Failed
    API-->>F: 303 to failure_url?status=FAILED&buy_order=...
  end
  opt User cancels
    PP-->>F: 302 to cancel_url recommend API return with paypal_cancel=1
    F->>API: GET /api/payments/tbk/return?paypal_cancel=1&token=order_id
    API-->>F: 303 to cancel_url front ?status=CANCELED&buy_order=...
  end
```

#### Stripe (Checkout)

```mermaid
sequenceDiagram
  autonumber
  participant F as Frontend Browser
  participant API as ninja-payments-api
  participant ST as Stripe

  F->>API: POST /api/payments (provider=stripe)
  API->>ST: Create Checkout Session
  ST-->>API: {session.url, session.id}
  API-->>F: {status: PENDING, redirect: {url: session.url, method: GET}}
  F->>ST: GET session.url (Checkout)
  alt User completes
    ST-->>F: 302 to success_url (front)
  else User cancels
    ST-->>F: 302 to cancel_url (front)
  end

  opt Webhook recommended
    ST-->>API: checkout.session.completed
    API->>API: Mark order AUTHORIZED
  end
```

Notes for Stripe Checkout
- The browser returns directly to your `success_url`/`cancel_url` (the API is not in that redirect), so your page will not receive `status` or `buy_order` via query params from the API like Webpay/PayPal.
- Recommended success URL: include the placeholder `{CHECKOUT_SESSION_ID}` so the frontend can read the Session id if needed, e.g. `http://localhost:3000/success.html?session_id={CHECKOUT_SESSION_ID}`.
- To query server state from the success page (optional): call `GET /api/payments/tbk/return?token=<session_id>`. The API will poll Stripe for the session status and return `{ "status": "AUTHORIZED|FAILED" }` as JSON (in addition to your webhook updating server state).
  - Tip: add `&format=json` to force a JSON response from the API and avoid browser redirects.
- En la mayoría de integraciones, Stripe redirige sin `status`/`buy_order` en la URL del front. Esto es esperado; confía en el webhook como fuente de verdad y/o implementa la consulta opcional descrita arriba.

Webhook strategy (source of truth)
- Stripe: usa el webhook `/api/payments/stripe/webhook` para confirmar finalización de Checkout y eventos posteriores.
- PayPal: usa `/api/payments/paypal/webhook` para capturar órdenes aprobadas y reflejar reembolsos cuando sea posible.
- Webpay: no ofrece webhooks públicos; el commit ocurre en el retorno (`/tbk/return`) y las anulaciones vía REST.

## Security

- Authentication: Bearer token on `POST /api/payments` and sensitive endpoints. Default token is configured via env (see below). Requests without a valid token receive HTTP 401.
- Verifier: app/utils/security.py:8
- Idempotency: optional `Idempotency-Key` header. If the same key is reused and the transaction already exists with a known redirect, the API returns the same `redirect` info and status instead of creating a new transaction. Extractor: app/utils/idempotency.py:8
- No card or PII data is handled by your API; Webpay handles sensitive data.

## Configuration

Environment variables (Pydantic settings; case-insensitive):
- `API_BEARER_TOKEN` (default `testtoken` in code, `.env.example` uses `dev-token`)
- `TBK_API_KEY_ID` (integration)
- `TBK_API_KEY_SECRET` (integration)
- `TBK_HOST` (default `https://webpay3gint.transbank.cl`)
- `TBK_API_BASE` (default `/rswebpaytransaction/api/webpay/v1.2`)
- `PROVIDER` (default `transbank`)
- `RETURN_URL` (fallback default: `http://localhost:8000/api/payments/tbk/return`)
- Stripe:
  - `STRIPE_SECRET_KEY` (test/live)
  - `STRIPE_WEBHOOK_SECRET` (if using webhooks)
- PayPal:
  - `PAYPAL_CLIENT_ID` (sandbox/live)
  - `PAYPAL_CLIENT_SECRET` (sandbox/live)
  - `PAYPAL_BASE_URL` (default sandbox: `https://api-m.sandbox.paypal.com`)

Notes:
- Extra keys in `.env` are ignored by design (we use Pydantic v2 with `extra="ignore"`). This allows keeping `.env.example` more comprehensive than current code.
- The code uses Transbank integration credentials and endpoints. Production will require live credentials and ensuring the integration type in the SDK is set appropriately.

## Logging

- JSON logs at INFO level. Formatter: app/logging.py:8
- Whitelisted fields: `buy_order`, `token`, `response_code`, `status`, `idempotency_key`, `endpoint`, `method`, `redirect_to`, `event`, `currency`, `amount`, `provider` (see app/logging.py:18).
- Typical messages: "transaction created", "transaction committed", "webpay status read", "webpay refund executed".

## Testing

- Tests use mocked PSPs. If DB variables are set, ensure PostgreSQL is up (see Docker Compose). To run tests without DB, unset `DB_HOST`.
- Run: `pytest`.

## Frontend Integration Tips

1) Create the payment on your backend and read `redirect.url` + `token` in response.
2) Render a form targeting `redirect.url` with hidden input `token_ws` = token.
3) Auto-submit the form (or present a pay button).
4) Handle the 303 redirect at your frontend `success_url`/`failure_url`/`cancel_url` and show a status page.
   - For PayPal, set `cancel_url` to the API return endpoint with `?paypal_cancel=1` so the API can mark the payment as canceled and then redirect to your front.
5) Configura y confía en webhooks para los flujos asíncronos (Stripe/PayPal). Para Webpay, el commit se realiza en el retorno y el front recibe `status` por query.

Minimal form example:

```html
<form id="pay" action="https://webpay3gint.transbank.cl/webpayserver/initTransaction" method="POST">
  <input type="hidden" name="token_ws" value="TOKEN" />
  <noscript><button type="submit">Pagar</button></noscript>
</form>
<script>document.getElementById('pay').submit();</script>
```

## Limitations & Next Steps

- Storage is in-memory; states are lost on process restart. For real use, persist to a DB and enforce idempotency with unique constraints.
- Currencies: Webpay requires `CLP`; PayPal Sandbox recomienda `USD`; Stripe soporta múltiples (CLP/ USD, etc.).
- Error handling es mínima; considera errores de dominio 4xx más específicos para producción.
- Providers: Webpay, Stripe y PayPal incluidos. Otros pueden agregarse vía la factory.
- Webhooks: Stripe implementado; opcional expandir a PayPal/otros.

## Postman & OpenAPI

- Import the included collection: `ninja-payments-api.postman_collection.json`.
- Or import the live OpenAPI spec from the running service: `http://localhost:8000/openapi.json`.

## Provider Setup Notes

Stripe (optional)
- Create or use a Stripe account in a supported country; enable Test mode.
- Get `sk_test_…` from Developers → API keys and set `STRIPE_SECRET_KEY`.
- For webhooks in local dev: `stripe listen --forward-to http://localhost:8000/api/payments/stripe/webhook` and set `STRIPE_WEBHOOK_SECRET`.

PayPal (recommended for countries without Stripe)
- Go to https://developer.paypal.com/, create a Developer account and a Sandbox app.
- Get your Sandbox `Client ID` and `Secret` and set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`.
- Keep `PAYPAL_BASE_URL` as `https://api-m.sandbox.paypal.com` for sandbox testing.
- Set `PAYPAL_WEBHOOK_ID` with your webhook id to enable signature verification.
- In requests, use `provider: "paypal"`. The API will return an approval URL; the frontend redirects there, and PayPal returns to your API `return_url` (commit) or to `cancel_url` (we recommend `.../api/payments/tbk/return?paypal_cancel=1`).

### Webhook Event → Payment Status

Stripe webhooks all point to `POST /api/payments/stripe/webhook`. The handler updates the `payment.status` column (and dispute/refund tables) as follows:

| Stripe event(s) | Resulting status | Notes |
| --- | --- | --- |
| `checkout.session.completed` | `AUTHORIZED` (if the PaymentIntent succeeds) / `FAILED` (otherwise) | Triggers `commit_payment`, which polls the session. |
| `checkout.session.expired` | `CANCELED` | Marks abandoned sessions. |
| `payment_intent.canceled` | `CANCELED` | Uses stored `payment_intent_id` metadata to locate the payment. |
| `payment_intent.payment_failed` | `FAILED` | Last payment error message is stored as the reason. |
| `charge.refunded`, `charge.refund.created`, `charge.refund.updated` | `REFUNDED` once the refunded amount covers the original charge; partial refunds keep the previous status but log the refund rows. |
| `charge.dispute.created`, `charge.dispute.updated`, `charge.dispute.funds_withdrawn` | `FAILED` | Also inserts/updates the dispute record. |
| `charge.dispute.closed` | `AUTHORIZED` when outcome is won; `FAILED` when lost. |
| `charge.dispute.funds_reinstated` | `AUTHORIZED` | Funds returned to the merchant. |

PayPal webhooks point to `POST /api/payments/paypal/webhook`. The handler keeps the payment row in sync and stores refunds/disputes:

| PayPal event(s) | Resulting status | Notes |
| --- | --- | --- |
| `CHECKOUT.ORDER.APPROVED` | `AUTHORIZED` / `FAILED` depending on capture result | Calls `commit_payment` to capture and persist status. |
| `PAYMENT.CAPTURE.COMPLETED` | `AUTHORIZED` (no change) | Capture info is logged for auditing. |
| `PAYMENT.CAPTURE.REFUNDED`, `PAYMENT.CAPTURE.PARTIALLY_REFUNDED` | `REFUNDED` | Amount stored in `refund` table; if partial, status stays `REFUNDED` with recorded amount. |
| `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REVERSED` | `FAILED` | Marks the payment as failed and logs the event. |
| `PAYMENT.CAPTURE.CANCELLED` (if emitted) | `CANCELED` | Some PayPal accounts send this instead of the cancel URL flow. |
| `CHECKOUT.ORDER.CANCELED` / `CHECKOUT.ORDER.CANCELLED` | `CANCELED` | Handles explicit cancels returned by PayPal. |
| `CUSTOMER.DISPUTE.*` | `FAILED` while open or lost; switches to `AUTHORIZED` when outcome is `RESOLVED_SELLER_FAVOUR` | All dispute payloads are stored in `payments.dispute`. |

## Repository Scope

This repository contains only the backend Payment API logic: creating transactions, communicating with PSPs (Webpay/Stripe/PayPal), handling the return/commit, webhooks, and refunds. Any demo frontend or reconciliation workers should live in separate repositories.

Notes:
- The API is CORS-enabled for development.
- Frontends should implement their own redirect handling and use provider webhooks (Stripe/PayPal) or the Webpay return commit.
- Reconciliation jobs should rely on PSP reports/APIs and webhook events; this API does not expose polling endpoints for status.


## Troubleshooting

- Stripe: la página de éxito queda “Cargando…”
  - Asegúrate de que `success_url` incluya `?session_id={CHECKOUT_SESSION_ID}`.
  - Tu front puede consultar `GET /api/payments/tbk/return?token=<session_id>&format=json` para confirmar estado (opcional) y, sobre todo, configurar el webhook.
  - Prueba rápido: `curl "http://localhost:8000/api/payments/tbk/return?token=cs_test_...&format=json"`.
  - Verifica el listener del webhook y `STRIPE_WEBHOOK_SECRET`; los logs deben mostrar “stripe webhook received” y “commit completed … AUTHORIZED/FAILED”.

- Stripe webhook no llega
  - `stripe login` y luego `stripe listen --events checkout.session.completed,payment_intent.payment_failed --forward-to http://localhost:8000/api/payments/stripe/webhook`.
  - Copia `whsec_...` a `.env` y reinicia la API.
  - Dispara un evento: `stripe trigger checkout.session.completed`.

- PayPal 422 Unprocessable Entity
  - Usa `currency: "USD"` en Sandbox y un `amount` pequeño (ej. 10).
  - Verifica `PAYPAL_CLIENT_ID` y `PAYPAL_CLIENT_SECRET`.

- Webpay 401 Unauthorized al crear
  - El header `Authorization: Bearer …` debe coincidir con `API_BEARER_TOKEN` del `.env`.
