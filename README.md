# ninja-payments-api

FastAPI service exposing a minimal payments API using Transbank Webpay Plus.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate || .\.venv\Scripts\activate
pip install -r requirements.txt
cp -n .env.example .env 2>/dev/null || copy .env.example .env
```

## Usage

Create a payment:

```bash
curl -X POST http://localhost:8000/api/payments \
  -H 'Authorization: Bearer testtoken' \
  -H 'Content-Type: application/json' \
  -d '{"buy_order":"o-123","amount":1000,"currency":"CLP","return_url":"http://localhost:8000/api/payments/tbk/return","success_url":"http://localhost:3000/checkout/success","failure_url":"http://localhost:3000/checkout/failure","cancel_url":"http://localhost:3000/checkout/canceled"}'
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

## Tests

```bash
pytest
```

## Overview

This service exposes a small, opinionated API to create payment transactions against Transbank Webpay Plus (integration environment), handle the browser return from Webpay, and report the final status back to the client. It favors simplicity and clear flows suitable for demos, PoCs, and local development.

Key traits:
- Minimal endpoints: create payment and handle the return.
- Real calls to Transbank integration for creation and commit.
- Optional frontend redirects after return (success/failure/cancel).
- Simple in‑memory store for payment state (ephemeral, dev‑only).
- Bearer token authentication for the create endpoint.
- JSON logging for easy parsing.

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
- Service: `app/services/payments_service.py` contains business rules.
- Provider: `app/providers/` contains provider implementations (Webpay, Stripe, PayPal).
- Store: `app/repositories/memory_store.py` keeps state per token in RAM.
- Config: `app/config.py` (Pydantic v2 settings; extra env vars are ignored).

## Domain & Statuses

```mermaid
classDiagram
  class Payment {
    +str id
    +str buy_order
    +int amount
    +Currency currency
    +PaymentStatus status
    +str? token
    +str? redirect_url
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
  }
  Payment --> PaymentStatus
```

State machine:

```mermaid
stateDiagram-v2
  [*] --> PENDING
  PENDING --> AUTHORIZED: commit response_code == 0
  PENDING --> FAILED: commit response_code != 0
  PENDING --> CANCELED: TBK_TOKEN cancel
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
  - `return_url` (string): where Webpay will redirect after payment
  - `provider` (string, optional): `webpay` (default), `stripe`, or `paypal`. Unsupported providers return 400.
  - `success_url` (string, optional): where to redirect the browser after an authorized payment
  - `failure_url` (string, optional): where to redirect the browser after a failed payment
  - `cancel_url` (string, optional): where to redirect the browser after a canceled payment

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

2) GET/POST `/api/payments/tbk/return` (Transbank Return)
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

3) GET `/health`
- Simple liveness check. Returns `{ "status": "ok" }`.

4) POST `/api/payments/stripe/webhook`
- Receives Stripe events, verifies the signature, and updates the payment state based on the Checkout Session id.
- Use for production-grade confirmation of Stripe payments.
- Local dev: `stripe listen --forward-to http://localhost:8000/api/payments/stripe/webhook` and set `STRIPE_WEBHOOK_SECRET`.

5) GET `/api/payments/pending`
- Returns a list of pending transactions (in-memory store) with minimal fields.
- Secured with Bearer token.

6) POST `/api/payments/refresh`
- Body: `{ "tokens": ["..."] }`
- For each token, queries the provider for read-only status when possible (Stripe/PayPal) and updates state. For Webpay, performs a commit to finalize.
- Returns `{ updated: n, results: { token: status } }`.

7) POST `/api/payments/status`
- Body: `{ "tokens": ["..."] }`
- Read-only status check. Returns provider-reported statuses without mutating the in-memory store. Values can be `AUTHORIZED`, `FAILED`, `CANCELED`, `PENDING`, or `null` if unknown/unavailable.

8) POST `/api/payments/refund`
- Auth: Bearer
- Body: `{ "token": "...", "amount": <int|null> }`
- Issues a refund with the provider associated to the token.
  - Stripe: refunds the PaymentIntent (amount in minor units for decimal currencies; zero-decimal for CLP). If omitted, full refund.
  - PayPal: refunds the latest capture of the Order (amount in major units; Sandbox typically USD). If omitted, full refund.
  - Webpay: not implemented.
- Response: `{ "status": "REFUNDED" | current_status }` (the in-memory store is updated to `REFUNDED` on success).

9) GET `/api/payments/redirect`
- Auth: Bearer
- Query: `?token=...`
- Returns the redirect information to resume a pending checkout flow:
  - Webpay: `{ url, token, method: POST, form_fields: { token_ws } }`
  - Stripe/PayPal: `{ url, token, method: GET }`

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
- In the included demo frontend, Stripe redirects back without `status`/`buy_order`, so those fields appear `null`. This is expected; rely on the webhook for the final truth or implement the optional query described above.

Refund visibility in status (read-only)
- PayPal: `/status` inspects captures and returns `REFUNDED` if any capture is refunded/partially refunded.
- Webpay: `/status` returns `null` (no read-only status); use `/refresh` to finalize or consult your store.
- Stripe: `/status` currently returns `AUTHORIZED` if paid; refunds may not appear as `REFUNDED` in this check. Use the refund endpoint response or the provider dashboard as source of truth, or extend the provider to inspect PaymentIntent refunds.

## Security

- Authentication: Bearer token on `POST /api/payments`. Default token is configured via env (see below). Requests without a valid token receive HTTP 401.
- Idempotency: optional `Idempotency-Key` header. If the same key is reused and the transaction already exists with a known redirect, the API returns the same `redirect` info and status instead of creating a new transaction.
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

- JSON logs at INFO level (`app/logging.py`).
- Notable fields: `message`, `buy_order`, `token`, `response_code`.
- Examples: "transaction created", "transaction committed".

## Testing

- `pytest` runs a smoke test mocking network calls and the Transbank SDK.
- No external connectivity is required to run tests.
- To run: `pytest`.

## Frontend Integration Tips

1) Create the payment on your backend and read `redirect.url` + `token` in response.
2) Render a form targeting `redirect.url` with hidden input `token_ws` = token.
3) Auto-submit the form (or present a pay button).
4) Handle the 303 redirect at your frontend `success_url`/`failure_url`/`cancel_url` and show a status page.
   - For PayPal, set `cancel_url` to the API return endpoint with `?paypal_cancel=1` so the API can mark the payment as canceled and then redirect to your front.

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
- In requests, use `provider: "paypal"`. The API will return an approval URL; the frontend redirects there, and PayPal returns to your API `return_url` (commit) or to `cancel_url` (we recommend `.../api/payments/tbk/return?paypal_cancel=1`).

## Demo Frontend

A minimal static frontend is included under `frontend/` to simulate the full flow end‑to‑end.

Run locally:

```bash
# in the repo root, with API running on :8000
python -m http.server 3000 -d frontend
# open http://localhost:3000 in your browser
```

Notes:
- The API has permissive CORS for development.
- Use the form to set API base (`http://localhost:8000`), bearer token, and select the provider (Webpay/Stripe/PayPal).
- On create:
  - Webpay: auto‑POST a Webpay form; return goes through the API, which redirects to `success.html`/`failure.html`/`canceled.html` with `status` and `buy_order`.
  - Stripe: navega a Checkout y vuelve a `success.html?session_id={...}`; esa página consulta la API para confirmar estado y lo muestra.
  - PayPal: navega al approve URL y vuelve vía API (commit) o cancel; la API redirige al front con `status` y `buy_order`.

## Reconciler (demo)

Included under `reconciler/`, a tiny polling worker to reconcile pending transactions by asking the API to refresh their state.

Run locally:

```bash
# in the same venv as the API
export API_BASE=http://localhost:8000
export API_TOKEN=dev-token
export INTERVAL_SECONDS=15
python -m reconciler.main
```

How it works:
- Calls `GET /api/payments/pending` to obtain tokens
- If `READ_ONLY=1` (default), calls `POST /api/payments/status` and prints current provider statuses without changing local state
- If `READ_ONLY=0`, calls `POST /api/payments/refresh` to update states (finalizes Webpay)
- Prints one JSON line with the result on each interval


## Troubleshooting

- Stripe success page muestra “Cargando…”
  - Asegúrate de que `success_url` incluya `?session_id={CHECKOUT_SESSION_ID}`.
  - La página `success.html` consulta `GET /api/payments/tbk/return?token=<session_id>&format=json` para confirmar el estado.
  - Prueba rápido: `curl "http://localhost:8000/api/payments/tbk/return?token=cs_test_...&format=json"`.
  - Revisa que el listener del webhook esté activo y `STRIPE_WEBHOOK_SECRET` configurado; los logs deben mostrar “stripe webhook received” y “commit completed … AUTHORIZED/FAILED”.

- Stripe webhook no llega
  - `stripe login` y luego `stripe listen --events checkout.session.completed,payment_intent.payment_failed --forward-to http://localhost:8000/api/payments/stripe/webhook`.
  - Copia `whsec_...` a `.env` y reinicia la API.
  - Dispara un evento: `stripe trigger checkout.session.completed`.

- PayPal 422 Unprocessable Entity
  - Usa `currency: "USD"` en Sandbox y un `amount` pequeño (ej. 10).
  - Verifica `PAYPAL_CLIENT_ID` y `PAYPAL_CLIENT_SECRET`.

- Webpay 401 Unauthorized al crear
  - El header `Authorization: Bearer …` debe coincidir con `API_BEARER_TOKEN` del `.env`.
