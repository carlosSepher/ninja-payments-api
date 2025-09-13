BEGIN;

-- Requisitos (para gen_random_uuid)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Esquema
CREATE SCHEMA IF NOT EXISTS payments;

-- Tipos
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'provider_type') THEN
    CREATE TYPE payments.provider_type AS ENUM ('webpay', 'stripe', 'paypal');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_status') THEN
    CREATE TYPE payments.payment_status AS ENUM ('PENDING','AUTHORIZED','FAILED','CANCELED','REFUNDED');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'event_type') THEN
    CREATE TYPE payments.event_type AS ENUM (
      'CREATED','COMMIT','CANCEL','FAILED','REFUND_REQUESTED','REFUNDED',
      'STATUS_READ','WEBHOOK','RECONCILE','MANUAL_UPDATE','ERROR'
    );
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'direction_type') THEN
    CREATE TYPE payments.direction_type AS ENUM ('INBOUND','OUTBOUND');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'operation_type') THEN
    CREATE TYPE payments.operation_type AS ENUM ('CREATE','COMMIT','CAPTURE','STATUS','REFUND','WEBHOOK');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'actor_type') THEN
    CREATE TYPE payments.actor_type AS ENUM ('API','WEBHOOK','RECONCILER','USER','SYSTEM');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'verification_status') THEN
    CREATE TYPE payments.verification_status AS ENUM ('SUCCESS','FAILURE','UNKNOWN');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'refund_status') THEN
    CREATE TYPE payments.refund_status AS ENUM ('REQUESTED','PENDING','SUCCEEDED','FAILED','CANCELED','PARTIAL');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'environment_type') THEN
    CREATE TYPE payments.environment_type AS ENUM ('test','live');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'order_status') THEN
    CREATE TYPE payments.order_status AS ENUM ('OPEN','COMPLETED','CANCELED','EXPIRED','PARTIAL');
  END IF;
END$$;

-- Cuentas de PSP (sin secretos)
CREATE TABLE IF NOT EXISTS payments.provider_account (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider            payments.provider_type NOT NULL,
  name                text NOT NULL,
  merchant_id         text,
  external_account_id text,
  environment         payments.environment_type NOT NULL DEFAULT 'test',
  active              boolean NOT NULL DEFAULT TRUE,
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, merchant_id, environment)
);

-- Orden de pago (una orden puede tener múltiples intentos de pago)
CREATE TABLE IF NOT EXISTS payments.payment_order (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  buy_order               text NOT NULL UNIQUE, -- Un único identificador de negocio (por ahora único global)
  environment             payments.environment_type NOT NULL DEFAULT 'test',
  currency                varchar(3) CHECK (currency IS NULL OR char_length(currency)=3),
  amount_expected_minor   bigint CHECK (amount_expected_minor IS NULL OR amount_expected_minor > 0),
  status                  payments.order_status NOT NULL DEFAULT 'OPEN',
  metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_payment_order_created_at ON payments.payment_order(created_at);
CREATE INDEX IF NOT EXISTS ix_payment_order_status ON payments.payment_order(status);

-- Transacciones/Intentos de pago
CREATE TABLE IF NOT EXISTS payments.payment (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_order_id      uuid NOT NULL REFERENCES payments.payment_order(id) ON DELETE CASCADE,
  buy_order             text NOT NULL,                      -- redundante para comodidad de consulta
  amount_minor          bigint NOT NULL CHECK (amount_minor > 0),
  currency              varchar(3) NOT NULL CHECK (char_length(currency)=3),
  provider              payments.provider_type NOT NULL,
  provider_account_id   uuid REFERENCES payments.provider_account(id),
  environment           payments.environment_type NOT NULL DEFAULT 'test',

  status                payments.payment_status NOT NULL DEFAULT 'PENDING',
  status_reason         text,
  authorization_code    text,
  response_code         integer,

  token                 text,                               -- token_ws / session_id / order_id
  redirect_url          text,
  return_url            text,
  success_url           text,
  failure_url           text,
  cancel_url            text,

  idempotency_key       text,
  provider_metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
  context               jsonb NOT NULL DEFAULT '{}'::jsonb,

  amount_refunded_minor bigint NOT NULL DEFAULT 0,
  first_authorized_at   timestamptz,
  canceled_at           timestamptz,
  failed_at             timestamptz,
  refunded_at           timestamptz,

  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);

-- Índices payment
CREATE INDEX IF NOT EXISTS ix_payment_order_id ON payments.payment(payment_order_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_provider_token ON payments.payment(provider, token) WHERE token IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_idempotency_key ON payments.payment(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_payment_buy_order ON payments.payment(buy_order);
CREATE INDEX IF NOT EXISTS ix_payment_provider_status ON payments.payment(provider, status);
CREATE INDEX IF NOT EXISTS ix_payment_created_at ON payments.payment(created_at);
CREATE INDEX IF NOT EXISTS ix_payment_provider_account ON payments.payment(provider_account_id);

-- Historial de estados
CREATE TABLE IF NOT EXISTS payments.payment_state_history (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id       uuid NOT NULL REFERENCES payments.payment(id) ON DELETE CASCADE,
  from_status      payments.payment_status,
  to_status        payments.payment_status NOT NULL,
  event_type       payments.event_type NOT NULL,
  response_code    integer,
  reason           text,
  actor_type       payments.actor_type NOT NULL,
  actor_id         text,
  provider         payments.provider_type,
  provider_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_state_history_payment ON payments.payment_state_history(payment_id, occurred_at DESC);

-- Eventos de integración (IO con PSP)
CREATE TABLE IF NOT EXISTS payments.provider_event_log (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id          uuid REFERENCES payments.payment(id) ON DELETE SET NULL,
  provider            payments.provider_type NOT NULL,
  provider_account_id uuid REFERENCES payments.provider_account(id) ON DELETE SET NULL,
  direction           payments.direction_type NOT NULL,
  operation           payments.operation_type NOT NULL,
  request_url         text,
  request_headers     jsonb NOT NULL DEFAULT '{}'::jsonb,
  request_body        jsonb,
  response_status     integer,
  response_headers    jsonb,
  response_body       jsonb,
  error_message       text,
  latency_ms          integer,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_event_log_payment ON payments.provider_event_log(payment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_event_log_provider ON payments.provider_event_log(provider, created_at DESC);

-- Bandeja de webhooks
CREATE TABLE IF NOT EXISTS payments.webhook_inbox (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider             payments.provider_type NOT NULL,
  event_id             text,
  event_type           text,
  verification_status  payments.verification_status NOT NULL DEFAULT 'UNKNOWN',
  headers              jsonb NOT NULL DEFAULT '{}'::jsonb,
  payload              jsonb NOT NULL,
  related_payment_id   uuid REFERENCES payments.payment(id) ON DELETE SET NULL,
  received_at          timestamptz NOT NULL DEFAULT now(),
  verified_at          timestamptz,
  processed_at         timestamptz,
  process_status       text,
  error_message        text,
  UNIQUE (provider, event_id)
);
CREATE INDEX IF NOT EXISTS ix_webhook_provider_received ON payments.webhook_inbox(provider, received_at DESC);
CREATE INDEX IF NOT EXISTS ix_webhook_related_payment ON payments.webhook_inbox(related_payment_id);

-- Refunds
CREATE TABLE IF NOT EXISTS payments.refund (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id         uuid NOT NULL REFERENCES payments.payment(id) ON DELETE CASCADE,
  provider           payments.provider_type NOT NULL,
  amount_minor       bigint NOT NULL CHECK (amount_minor > 0),
  status             payments.refund_status NOT NULL DEFAULT 'REQUESTED',
  provider_refund_id text,
  reason             text,
  requested_at       timestamptz NOT NULL DEFAULT now(),
  confirmed_at       timestamptz,
  payload            jsonb,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_refund_payment ON payments.refund(payment_id, created_at DESC);

-- Registros de chequeos (para reconciliador)
CREATE TABLE IF NOT EXISTS payments.status_check (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id       uuid NOT NULL REFERENCES payments.payment(id) ON DELETE CASCADE,
  provider         payments.provider_type NOT NULL,
  requested_at     timestamptz NOT NULL DEFAULT now(),
  success          boolean,
  provider_status  text,
  mapped_status    payments.payment_status,
  response_code    integer,
  error_message    text,
  raw_payload      jsonb,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_status_check_payment ON payments.status_check(payment_id, requested_at DESC);

-- Disputas / chargebacks (opcional)
CREATE TABLE IF NOT EXISTS payments.dispute (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id          uuid NOT NULL REFERENCES payments.payment(id) ON DELETE CASCADE,
  provider            payments.provider_type NOT NULL,
  provider_dispute_id text,
  status              text,
  amount_minor        bigint,
  reason              text,
  opened_at           timestamptz,
  closed_at           timestamptz,
  payload             jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dispute_payment ON payments.dispute(payment_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dispute_provider_id ON payments.dispute(provider, provider_dispute_id) WHERE provider_dispute_id IS NOT NULL;

-- Liquidaciones / clearing (opcional; para reconciliación por archivos)
CREATE TABLE IF NOT EXISTS payments.settlement_batch (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider      payments.provider_type NOT NULL,
  batch_date    date NOT NULL,
  currency      varchar(3) CHECK (currency IS NULL OR char_length(currency)=3),
  source        text,                       -- archivo/origen
  notes         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, batch_date, source)
);

CREATE TABLE IF NOT EXISTS payments.settlement_item (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id         uuid NOT NULL REFERENCES payments.settlement_batch(id) ON DELETE CASCADE,
  payment_id       uuid REFERENCES payments.payment(id) ON DELETE SET NULL,
  buy_order        text,
  provider_token   text,
  status_text      text,               -- estado en archivo
  gross_minor      bigint,
  fee_minor        bigint,
  net_minor        bigint,
  payload          jsonb,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_settlement_item_batch ON payments.settlement_item(batch_id);
CREATE INDEX IF NOT EXISTS ix_settlement_item_payment ON payments.settlement_item(payment_id);

-- Triggers utilitarios
CREATE OR REPLACE FUNCTION payments.fn_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END$$;

-- Historial al crear PENDING
CREATE OR REPLACE FUNCTION payments.fn_log_initial_state()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO payments.payment_state_history(payment_id, from_status, to_status, event_type, actor_type, provider, occurred_at)
  VALUES (NEW.id, NULL, NEW.status, 'CREATED', 'API', NEW.provider, now());
  RETURN NEW;
END$$;

-- Historial en cambios de estado
CREATE OR REPLACE FUNCTION payments.fn_log_status_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO payments.payment_state_history(
      payment_id, from_status, to_status, event_type, response_code, reason, actor_type, provider, occurred_at
    )
    VALUES (
      NEW.id, OLD.status, NEW.status,
      CASE
        WHEN NEW.status='AUTHORIZED' THEN 'COMMIT'
        WHEN NEW.status='FAILED'     THEN 'FAILED'
        WHEN NEW.status='CANCELED'   THEN 'CANCEL'
        WHEN NEW.status='REFUNDED'   THEN 'REFUNDED'
        ELSE 'MANUAL_UPDATE'
      END,
      COALESCE(NEW.response_code, OLD.response_code),
      COALESCE(NEW.status_reason, OLD.status_reason),
      'SYSTEM', NEW.provider, now()
    );
  END IF;
  RETURN NEW;
END$$;

-- Enlazar triggers
DROP TRIGGER IF EXISTS trg_payment_order_touch ON payments.payment_order;
CREATE TRIGGER trg_payment_order_touch BEFORE UPDATE ON payments.payment_order
FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_payment_touch ON payments.payment;
CREATE TRIGGER trg_payment_touch BEFORE UPDATE ON payments.payment
FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_refund_touch ON payments.refund;
CREATE TRIGGER trg_refund_touch BEFORE UPDATE ON payments.refund
FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_dispute_touch ON payments.dispute;
CREATE TRIGGER trg_dispute_touch BEFORE UPDATE ON payments.dispute
FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();

DROP TRIGGER IF EXISTS trg_payment_initial_state ON payments.payment;
CREATE TRIGGER trg_payment_initial_state AFTER INSERT ON payments.payment
FOR EACH ROW EXECUTE FUNCTION payments.fn_log_initial_state();

DROP TRIGGER IF EXISTS trg_payment_status_change ON payments.payment;
CREATE TRIGGER trg_payment_status_change AFTER UPDATE OF status ON payments.payment
FOR EACH ROW EXECUTE FUNCTION payments.fn_log_status_change();

-- Vistas (KPIs / trazabilidad)
CREATE OR REPLACE VIEW payments.v_payments_daily_kpis AS
SELECT
  date_trunc('day', created_at)::date AS day,
  provider,
  status,
  count(*) AS tx_count,
  sum(amount_minor) AS amount_sum_minor,
  sum(amount_refunded_minor) AS refunded_sum_minor
FROM payments.payment
GROUP BY 1,2,3;

CREATE OR REPLACE VIEW payments.v_payment_last_event AS
SELECT
  p.id AS payment_id,
  p.buy_order,
  p.provider,
  p.status,
  (SELECT e.operation FROM payments.provider_event_log e WHERE e.payment_id=p.id ORDER BY e.created_at DESC LIMIT 1) AS last_operation,
  (SELECT e.created_at FROM payments.provider_event_log e WHERE e.payment_id=p.id ORDER BY e.created_at DESC LIMIT 1) AS last_operation_at
FROM payments.payment p;

CREATE OR REPLACE VIEW payments.v_order_summary AS
SELECT
  o.id AS payment_order_id,
  o.buy_order,
  o.status AS order_status,
  o.amount_expected_minor,
  o.currency,
  o.created_at,
  count(p.id)                       AS attempts,
  sum((p.status='AUTHORIZED')::int) AS authorized_attempts,
  sum(p.amount_minor)               AS total_amount_attempts_minor,
  sum(p.amount_refunded_minor)      AS total_refunded_minor
FROM payments.payment_order o
LEFT JOIN payments.payment p ON p.payment_order_id = o.id
GROUP BY 1,2,3,4,5,6;

COMMIT;

-- Notas:
-- - No almacenar PII ni secretos en columnas JSONB; sanitizar en la app.
-- - amount_minor usa unidades menores (CLP: zero-decimal). currency ISO-4217 de 3 letras.
-- - payment_order.buy_order es único (por ahora); múltiples intentos (payment) pueden referenciarlo.
-- - settlement_* es opcional para carga de archivos de clearing.

