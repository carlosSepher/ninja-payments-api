--
-- PostgreSQL database dump
--

\restrict LXci9fEQhy4JFHDddIHI4oxKuv3aVOTcs6JjkynqA4qnTIp0v0FA5nrnXi09Pzb

-- Dumped from database version 16.10 (Debian 16.10-1.pgdg13+1)
-- Dumped by pg_dump version 16.10 (Debian 16.10-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: payments; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA payments;


--
-- Name: actor_type; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.actor_type AS ENUM (
    'API',
    'WEBHOOK',
    'RECONCILER',
    'USER',
    'SYSTEM'
);


--
-- Name: direction_type; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.direction_type AS ENUM (
    'INBOUND',
    'OUTBOUND'
);


--
-- Name: environment_type; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.environment_type AS ENUM (
    'test',
    'live'
);


--
-- Name: event_type; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.event_type AS ENUM (
    'CREATED',
    'COMMIT',
    'CANCEL',
    'FAILED',
    'REFUND_REQUESTED',
    'REFUNDED',
    'STATUS_READ',
    'WEBHOOK',
    'RECONCILE',
    'MANUAL_UPDATE',
    'ERROR'
);


--
-- Name: operation_type; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.operation_type AS ENUM (
    'CREATE',
    'COMMIT',
    'CAPTURE',
    'STATUS',
    'REFUND',
    'WEBHOOK'
);


--
-- Name: order_status; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.order_status AS ENUM (
    'OPEN',
    'COMPLETED',
    'CANCELED',
    'EXPIRED',
    'PARTIAL'
);


--
-- Name: payment_method_type; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.payment_method_type AS ENUM (
    'credito',
    'debito',
    'prepago',
    'desconocido'
);


--
-- Name: payment_status; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.payment_status AS ENUM (
    'PENDING',
    'AUTHORIZED',
    'FAILED',
    'CANCELED',
    'REFUNDED',
    'TO_CONFIRM',
    'ABANDONED'
);


--
-- Name: provider_type; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.provider_type AS ENUM (
    'webpay',
    'stripe',
    'paypal'
);


--
-- Name: refund_status; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.refund_status AS ENUM (
    'REQUESTED',
    'PENDING',
    'SUCCEEDED',
    'FAILED',
    'CANCELED',
    'PARTIAL'
);


--
-- Name: runtime_event_type; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.runtime_event_type AS ENUM (
    'STARTUP',
    'SHUTDOWN',
    'HEARTBEAT',
    'GREETING',
    'CONFIG_CHECK'
);


--
-- Name: verification_status; Type: TYPE; Schema: payments; Owner: -
--

CREATE TYPE payments.verification_status AS ENUM (
    'SUCCESS',
    'FAILURE',
    'UNKNOWN'
);


--
-- Name: fn_log_initial_state(); Type: FUNCTION; Schema: payments; Owner: -
--

CREATE FUNCTION payments.fn_log_initial_state() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  INSERT INTO payments.payment_state_history(payment_id, from_status, to_status, event_type, actor_type, provider, occurred_at)
  VALUES (NEW.id, NULL, NEW.status, 'CREATED', 'API', NEW.provider, now());
  RETURN NEW;
END$$;


--
-- Name: fn_log_status_change(); Type: FUNCTION; Schema: payments; Owner: -
--

CREATE FUNCTION payments.fn_log_status_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO payments.payment_state_history(
      payment_id, from_status, to_status, event_type, response_code, reason, actor_type, provider, occurred_at
    )
    VALUES (
      NEW.id, OLD.status, NEW.status,
      (CASE
        WHEN NEW.status='AUTHORIZED' THEN 'COMMIT'
        WHEN NEW.status='FAILED'     THEN 'FAILED'
        WHEN NEW.status='CANCELED'   THEN 'CANCEL'
        WHEN NEW.status='REFUNDED'   THEN 'REFUNDED'
        ELSE 'MANUAL_UPDATE'
      END)::payments.event_type,
      COALESCE(NEW.response_code, OLD.response_code),
      COALESCE(NEW.status_reason, OLD.status_reason),
      'SYSTEM', NEW.provider, now()
    );
  END IF;
  RETURN NEW;
END$$;


--
-- Name: fn_touch_updated_at(); Type: FUNCTION; Schema: payments; Owner: -
--

CREATE FUNCTION payments.fn_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: company; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.company (
    id bigint NOT NULL,
    name text NOT NULL,
    contact_email text,
    api_token text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: company_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.company_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: company_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.company_id_seq OWNED BY payments.company.id;


--
-- Name: crm_event_log; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.crm_event_log (
    id integer NOT NULL,
    payment_id integer NOT NULL,
    operation character varying(50) NOT NULL,
    request_url text NOT NULL,
    request_headers jsonb NOT NULL,
    request_body jsonb,
    response_status integer,
    response_headers jsonb,
    response_body jsonb,
    error_message text,
    latency_ms integer,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: crm_event_log_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.crm_event_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: crm_event_log_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.crm_event_log_id_seq OWNED BY payments.crm_event_log.id;


--
-- Name: crm_push_queue; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.crm_push_queue (
    id integer NOT NULL,
    payment_id integer NOT NULL,
    operation character varying(50) NOT NULL,
    status character varying(50) DEFAULT 'PENDING'::character varying NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp without time zone,
    last_attempt_at timestamp without time zone,
    response_code integer,
    crm_id character varying(255),
    last_error text,
    payload jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: crm_push_queue_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.crm_push_queue_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: crm_push_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.crm_push_queue_id_seq OWNED BY payments.crm_push_queue.id;


--
-- Name: dispute; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.dispute (
    id bigint NOT NULL,
    payment_id bigint NOT NULL,
    provider payments.provider_type NOT NULL,
    provider_dispute_id text,
    status text,
    amount_minor bigint,
    reason text,
    opened_at timestamp with time zone,
    closed_at timestamp with time zone,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dispute_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.dispute_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dispute_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.dispute_id_seq OWNED BY payments.dispute.id;


--
-- Name: payment; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.payment (
    id bigint NOT NULL,
    payment_order_id bigint NOT NULL,
    company_id bigint NOT NULL,
    buy_order text NOT NULL,
    amount_minor bigint NOT NULL,
    currency character varying(3) NOT NULL,
    provider payments.provider_type NOT NULL,
    payment_type payments.payment_method_type NOT NULL,
    commerce_id text NOT NULL,
    product_id text NOT NULL,
    product_name text NOT NULL,
    provider_account_id bigint,
    environment payments.environment_type DEFAULT 'test'::payments.environment_type NOT NULL,
    status payments.payment_status DEFAULT 'PENDING'::payments.payment_status NOT NULL,
    status_reason text,
    authorization_code text,
    response_code integer,
    token text,
    redirect_url text,
    return_url text,
    success_url text,
    failure_url text,
    cancel_url text,
    idempotency_key text,
    provider_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    amount_refunded_minor bigint DEFAULT 0 NOT NULL,
    first_authorized_at timestamp with time zone,
    canceled_at timestamp with time zone,
    failed_at timestamp with time zone,
    refunded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT payment_amount_minor_check CHECK ((amount_minor > 0)),
    CONSTRAINT payment_currency_check CHECK ((char_length((currency)::text) = 3))
);


--
-- Name: payment_contract; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.payment_contract (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    payment_id bigint NOT NULL,
    notifica boolean DEFAULT false NOT NULL,
    contrato numeric(20,0) DEFAULT 0 NOT NULL,
    cuotas integer[] DEFAULT '{}'::integer[] NOT NULL,
    tipo_pago text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT payment_contract_payment_id_key UNIQUE (payment_id),
    CONSTRAINT payment_contract_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id) ON DELETE CASCADE
);


--
-- Name: payment_deposit_info; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.payment_deposit_info (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    payment_id bigint NOT NULL,
    nombre_depositante text,
    rut_depositante text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT payment_deposit_info_payment_id_key UNIQUE (payment_id),
    CONSTRAINT payment_deposit_info_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id) ON DELETE CASCADE
);


--
-- Name: payment_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.payment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: payment_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.payment_id_seq OWNED BY payments.payment.id;


--
-- Name: payment_order; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.payment_order (
    id bigint NOT NULL,
    company_id bigint NOT NULL,
    buy_order text NOT NULL,
    environment payments.environment_type DEFAULT 'test'::payments.environment_type NOT NULL,
    currency character varying(3),
    amount_expected_minor bigint,
    status payments.order_status DEFAULT 'OPEN'::payments.order_status NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    customer_rut text,
    CONSTRAINT payment_order_amount_expected_minor_check CHECK (((amount_expected_minor IS NULL) OR (amount_expected_minor > 0))),
    CONSTRAINT payment_order_currency_check CHECK (((currency IS NULL) OR (char_length((currency)::text) = 3)))
);


--
-- Name: payment_order_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.payment_order_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: payment_order_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.payment_order_id_seq OWNED BY payments.payment_order.id;


--
-- Name: payment_state_history; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.payment_state_history (
    id bigint NOT NULL,
    payment_id bigint NOT NULL,
    from_status payments.payment_status,
    to_status payments.payment_status NOT NULL,
    event_type payments.event_type NOT NULL,
    response_code integer,
    reason text,
    actor_type payments.actor_type NOT NULL,
    actor_id text,
    provider payments.provider_type,
    provider_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: payment_state_history_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.payment_state_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: payment_state_history_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.payment_state_history_id_seq OWNED BY payments.payment_state_history.id;


--
-- Name: provider_account; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.provider_account (
    id bigint NOT NULL,
    provider payments.provider_type NOT NULL,
    name text NOT NULL,
    merchant_id text,
    external_account_id text,
    environment payments.environment_type DEFAULT 'test'::payments.environment_type NOT NULL,
    active boolean DEFAULT true NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: provider_account_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.provider_account_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: provider_account_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.provider_account_id_seq OWNED BY payments.provider_account.id;


--
-- Name: provider_event_log; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.provider_event_log (
    id bigint NOT NULL,
    payment_id bigint,
    provider payments.provider_type NOT NULL,
    provider_account_id bigint,
    direction payments.direction_type NOT NULL,
    operation payments.operation_type NOT NULL,
    request_url text,
    request_headers jsonb DEFAULT '{}'::jsonb NOT NULL,
    request_body jsonb,
    response_status integer,
    response_headers jsonb,
    response_body jsonb,
    error_message text,
    latency_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: provider_event_log_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.provider_event_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: provider_event_log_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.provider_event_log_id_seq OWNED BY payments.provider_event_log.id;


--
-- Name: refund; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.refund (
    id bigint NOT NULL,
    payment_id bigint NOT NULL,
    provider payments.provider_type NOT NULL,
    amount_minor bigint NOT NULL,
    status payments.refund_status DEFAULT 'REQUESTED'::payments.refund_status NOT NULL,
    provider_refund_id text,
    reason text,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_at timestamp with time zone,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT refund_amount_minor_check CHECK ((amount_minor > 0))
);


--
-- Name: refund_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.refund_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: refund_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.refund_id_seq OWNED BY payments.refund.id;


--
-- Name: service_runtime_log; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.service_runtime_log (
    id bigint NOT NULL,
    instance_id text NOT NULL,
    host_name text,
    process_id integer,
    service_version text,
    git_sha text,
    event_type payments.runtime_event_type NOT NULL,
    uptime_seconds bigint,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: service_runtime_log_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.service_runtime_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: service_runtime_log_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.service_runtime_log_id_seq OWNED BY payments.service_runtime_log.id;


--
-- Name: settlement_batch; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.settlement_batch (
    id bigint NOT NULL,
    provider payments.provider_type NOT NULL,
    batch_date date NOT NULL,
    currency character varying(3),
    source text,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT settlement_batch_currency_check CHECK (((currency IS NULL) OR (char_length((currency)::text) = 3)))
);


--
-- Name: settlement_batch_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.settlement_batch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: settlement_batch_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.settlement_batch_id_seq OWNED BY payments.settlement_batch.id;


--
-- Name: settlement_item; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.settlement_item (
    id bigint NOT NULL,
    batch_id bigint NOT NULL,
    payment_id bigint,
    buy_order text,
    provider_token text,
    status_text text,
    gross_minor bigint,
    fee_minor bigint,
    net_minor bigint,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: settlement_item_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.settlement_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: settlement_item_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.settlement_item_id_seq OWNED BY payments.settlement_item.id;


--
-- Name: status_check; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.status_check (
    id bigint NOT NULL,
    payment_id bigint NOT NULL,
    provider payments.provider_type NOT NULL,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    success boolean,
    provider_status text,
    mapped_status payments.payment_status,
    response_code integer,
    error_message text,
    raw_payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: status_check_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.status_check_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: status_check_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.status_check_id_seq OWNED BY payments.status_check.id;


--
-- Name: user_account; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.user_account (
    id bigint NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(512) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_account_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.user_account_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_account_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.user_account_id_seq OWNED BY payments.user_account.id;


--
-- Name: v_order_summary; Type: VIEW; Schema: payments; Owner: -
--

CREATE VIEW payments.v_order_summary AS
 SELECT o.id AS payment_order_id,
    o.buy_order,
    o.status AS order_status,
    o.amount_expected_minor,
    o.currency,
    o.created_at,
    count(p.id) AS attempts,
    sum(((p.status = 'AUTHORIZED'::payments.payment_status))::integer) AS authorized_attempts,
    sum(p.amount_minor) AS total_amount_attempts_minor,
    sum(p.amount_refunded_minor) AS total_refunded_minor
   FROM (payments.payment_order o
     LEFT JOIN payments.payment p ON ((p.payment_order_id = o.id)))
  GROUP BY o.id, o.buy_order, o.status, o.amount_expected_minor, o.currency, o.created_at;


--
-- Name: v_payment_last_event; Type: VIEW; Schema: payments; Owner: -
--

CREATE VIEW payments.v_payment_last_event AS
 SELECT id AS payment_id,
    buy_order,
    provider,
    status,
    ( SELECT e.operation
           FROM payments.provider_event_log e
          WHERE (e.payment_id = p.id)
          ORDER BY e.created_at DESC
         LIMIT 1) AS last_operation,
    ( SELECT e.created_at
           FROM payments.provider_event_log e
          WHERE (e.payment_id = p.id)
          ORDER BY e.created_at DESC
         LIMIT 1) AS last_operation_at
   FROM payments.payment p;


--
-- Name: v_payments_daily_kpis; Type: VIEW; Schema: payments; Owner: -
--

CREATE VIEW payments.v_payments_daily_kpis AS
 SELECT (date_trunc('day'::text, created_at))::date AS day,
    provider,
    status,
    count(*) AS tx_count,
    sum(amount_minor) AS amount_sum_minor,
    sum(amount_refunded_minor) AS refunded_sum_minor
   FROM payments.payment
  GROUP BY ((date_trunc('day'::text, created_at))::date), provider, status;


--
-- Name: webhook_inbox; Type: TABLE; Schema: payments; Owner: -
--

CREATE TABLE payments.webhook_inbox (
    id bigint NOT NULL,
    provider payments.provider_type NOT NULL,
    event_id text,
    event_type text,
    verification_status payments.verification_status DEFAULT 'UNKNOWN'::payments.verification_status NOT NULL,
    headers jsonb DEFAULT '{}'::jsonb NOT NULL,
    payload jsonb NOT NULL,
    related_payment_id bigint,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    verified_at timestamp with time zone,
    processed_at timestamp with time zone,
    process_status text,
    error_message text
);


--
-- Name: webhook_inbox_id_seq; Type: SEQUENCE; Schema: payments; Owner: -
--

CREATE SEQUENCE payments.webhook_inbox_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: webhook_inbox_id_seq; Type: SEQUENCE OWNED BY; Schema: payments; Owner: -
--

ALTER SEQUENCE payments.webhook_inbox_id_seq OWNED BY payments.webhook_inbox.id;


--
-- Name: company id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.company ALTER COLUMN id SET DEFAULT nextval('payments.company_id_seq'::regclass);


--
-- Name: crm_event_log id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.crm_event_log ALTER COLUMN id SET DEFAULT nextval('payments.crm_event_log_id_seq'::regclass);


--
-- Name: crm_push_queue id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.crm_push_queue ALTER COLUMN id SET DEFAULT nextval('payments.crm_push_queue_id_seq'::regclass);


--
-- Name: dispute id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.dispute ALTER COLUMN id SET DEFAULT nextval('payments.dispute_id_seq'::regclass);


--
-- Name: payment id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment ALTER COLUMN id SET DEFAULT nextval('payments.payment_id_seq'::regclass);


--
-- Name: payment_order id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment_order ALTER COLUMN id SET DEFAULT nextval('payments.payment_order_id_seq'::regclass);


--
-- Name: payment_state_history id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment_state_history ALTER COLUMN id SET DEFAULT nextval('payments.payment_state_history_id_seq'::regclass);


--
-- Name: provider_account id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.provider_account ALTER COLUMN id SET DEFAULT nextval('payments.provider_account_id_seq'::regclass);


--
-- Name: provider_event_log id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.provider_event_log ALTER COLUMN id SET DEFAULT nextval('payments.provider_event_log_id_seq'::regclass);


--
-- Name: refund id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.refund ALTER COLUMN id SET DEFAULT nextval('payments.refund_id_seq'::regclass);


--
-- Name: service_runtime_log id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.service_runtime_log ALTER COLUMN id SET DEFAULT nextval('payments.service_runtime_log_id_seq'::regclass);


--
-- Name: settlement_batch id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.settlement_batch ALTER COLUMN id SET DEFAULT nextval('payments.settlement_batch_id_seq'::regclass);


--
-- Name: settlement_item id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.settlement_item ALTER COLUMN id SET DEFAULT nextval('payments.settlement_item_id_seq'::regclass);


--
-- Name: status_check id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.status_check ALTER COLUMN id SET DEFAULT nextval('payments.status_check_id_seq'::regclass);


--
-- Name: user_account id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.user_account ALTER COLUMN id SET DEFAULT nextval('payments.user_account_id_seq'::regclass);


--
-- Name: webhook_inbox id; Type: DEFAULT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.webhook_inbox ALTER COLUMN id SET DEFAULT nextval('payments.webhook_inbox_id_seq'::regclass);


--
-- Name: company company_api_token_key; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.company
    ADD CONSTRAINT company_api_token_key UNIQUE (api_token);


--
-- Name: company company_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.company
    ADD CONSTRAINT company_pkey PRIMARY KEY (id);


--
-- Name: crm_event_log crm_event_log_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.crm_event_log
    ADD CONSTRAINT crm_event_log_pkey PRIMARY KEY (id);


--
-- Name: crm_push_queue crm_push_queue_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.crm_push_queue
    ADD CONSTRAINT crm_push_queue_pkey PRIMARY KEY (id);


--
-- Name: dispute dispute_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.dispute
    ADD CONSTRAINT dispute_pkey PRIMARY KEY (id);


--
-- Name: payment_order payment_order_company_id_buy_order_key; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment_order
    ADD CONSTRAINT payment_order_company_id_buy_order_key UNIQUE (company_id, buy_order);


--
-- Name: payment_order payment_order_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment_order
    ADD CONSTRAINT payment_order_pkey PRIMARY KEY (id);


--
-- Name: payment payment_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment
    ADD CONSTRAINT payment_pkey PRIMARY KEY (id);


--
-- Name: payment_state_history payment_state_history_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment_state_history
    ADD CONSTRAINT payment_state_history_pkey PRIMARY KEY (id);


--
-- Name: provider_account provider_account_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.provider_account
    ADD CONSTRAINT provider_account_pkey PRIMARY KEY (id);


--
-- Name: provider_account provider_account_provider_merchant_id_environment_key; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.provider_account
    ADD CONSTRAINT provider_account_provider_merchant_id_environment_key UNIQUE (provider, merchant_id, environment);


--
-- Name: provider_event_log provider_event_log_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.provider_event_log
    ADD CONSTRAINT provider_event_log_pkey PRIMARY KEY (id);


--
-- Name: refund refund_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.refund
    ADD CONSTRAINT refund_pkey PRIMARY KEY (id);


--
-- Name: service_runtime_log service_runtime_log_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.service_runtime_log
    ADD CONSTRAINT service_runtime_log_pkey PRIMARY KEY (id);


--
-- Name: settlement_batch settlement_batch_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.settlement_batch
    ADD CONSTRAINT settlement_batch_pkey PRIMARY KEY (id);


--
-- Name: settlement_batch settlement_batch_provider_batch_date_source_key; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.settlement_batch
    ADD CONSTRAINT settlement_batch_provider_batch_date_source_key UNIQUE (provider, batch_date, source);


--
-- Name: settlement_item settlement_item_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.settlement_item
    ADD CONSTRAINT settlement_item_pkey PRIMARY KEY (id);


--
-- Name: status_check status_check_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.status_check
    ADD CONSTRAINT status_check_pkey PRIMARY KEY (id);


--
-- Name: crm_push_queue uq_crm_push_queue_payment_operation; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.crm_push_queue
    ADD CONSTRAINT uq_crm_push_queue_payment_operation UNIQUE (payment_id, operation);


--
-- Name: user_account user_account_email_key; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.user_account
    ADD CONSTRAINT user_account_email_key UNIQUE (email);


--
-- Name: user_account user_account_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.user_account
    ADD CONSTRAINT user_account_pkey PRIMARY KEY (id);


--
-- Name: webhook_inbox webhook_inbox_pkey; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.webhook_inbox
    ADD CONSTRAINT webhook_inbox_pkey PRIMARY KEY (id);


--
-- Name: webhook_inbox webhook_inbox_provider_event_id_key; Type: CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.webhook_inbox
    ADD CONSTRAINT webhook_inbox_provider_event_id_key UNIQUE (provider, event_id);


--
-- Name: idx_crm_event_log_created_at; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX idx_crm_event_log_created_at ON payments.crm_event_log USING btree (created_at);


--
-- Name: idx_crm_event_log_payment_id; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX idx_crm_event_log_payment_id ON payments.crm_event_log USING btree (payment_id);


--
-- Name: idx_crm_push_queue_created_at; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX idx_crm_push_queue_created_at ON payments.crm_push_queue USING btree (created_at);


--
-- Name: idx_crm_push_queue_next_attempt; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX idx_crm_push_queue_next_attempt ON payments.crm_push_queue USING btree (next_attempt_at) WHERE (next_attempt_at IS NOT NULL);


--
-- Name: idx_crm_push_queue_status; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX idx_crm_push_queue_status ON payments.crm_push_queue USING btree (status);


--
-- Name: idx_user_account_email_lower; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX idx_user_account_email_lower ON payments.user_account USING btree (lower((email)::text));


--
-- Name: ix_company_active; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_company_active ON payments.company USING btree (active);


--
-- Name: ix_company_name; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_company_name ON payments.company USING btree (name);


--
-- Name: ix_dispute_payment; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_dispute_payment ON payments.dispute USING btree (payment_id);


--
-- Name: ix_event_log_payment; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_event_log_payment ON payments.provider_event_log USING btree (payment_id, created_at DESC);


--
-- Name: ix_event_log_provider; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_event_log_provider ON payments.provider_event_log USING btree (provider, created_at DESC);


--
-- Name: ix_payment_buy_order; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_buy_order ON payments.payment USING btree (buy_order);


--
-- Name: ix_payment_commerce; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_commerce ON payments.payment USING btree (commerce_id);


--
-- Name: ix_payment_company; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_company ON payments.payment USING btree (company_id);


--
-- Name: ix_payment_created_at; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_created_at ON payments.payment USING btree (created_at);


--
-- Name: ix_payment_order_company; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_order_company ON payments.payment_order USING btree (company_id);


--
-- Name: ix_payment_order_created_at; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_order_created_at ON payments.payment_order USING btree (created_at);


--
-- Name: ix_payment_order_id; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_order_id ON payments.payment USING btree (payment_order_id);


--
-- Name: ix_payment_order_status; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_order_status ON payments.payment_order USING btree (status);


--
-- Name: ix_payment_product; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_product ON payments.payment USING btree (product_id);


--
-- Name: ix_payment_provider_account; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_provider_account ON payments.payment USING btree (provider_account_id);


--
-- Name: ix_payment_provider_status; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_payment_provider_status ON payments.payment USING btree (provider, status);


--
-- Name: ix_refund_payment; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_refund_payment ON payments.refund USING btree (payment_id, created_at DESC);


--
-- Name: ix_service_runtime_log_event_time; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_service_runtime_log_event_time ON payments.service_runtime_log USING btree (event_type, recorded_at DESC);


--
-- Name: ix_service_runtime_log_instance; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_service_runtime_log_instance ON payments.service_runtime_log USING btree (instance_id, recorded_at DESC);


--
-- Name: ix_settlement_item_batch; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_settlement_item_batch ON payments.settlement_item USING btree (batch_id);


--
-- Name: ix_settlement_item_payment; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_settlement_item_payment ON payments.settlement_item USING btree (payment_id);


--
-- Name: ix_state_history_payment; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_state_history_payment ON payments.payment_state_history USING btree (payment_id, occurred_at DESC);


--
-- Name: ix_status_check_payment; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_status_check_payment ON payments.status_check USING btree (payment_id, requested_at DESC);


--
-- Name: ix_webhook_provider_received; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_webhook_provider_received ON payments.webhook_inbox USING btree (provider, received_at DESC);


--
-- Name: ix_webhook_related_payment; Type: INDEX; Schema: payments; Owner: -
--

CREATE INDEX ix_webhook_related_payment ON payments.webhook_inbox USING btree (related_payment_id);


--
-- Name: ux_dispute_provider_id; Type: INDEX; Schema: payments; Owner: -
--

CREATE UNIQUE INDEX ux_dispute_provider_id ON payments.dispute USING btree (provider, provider_dispute_id) WHERE (provider_dispute_id IS NOT NULL);


--
-- Name: ux_payment_company_idempotency; Type: INDEX; Schema: payments; Owner: -
--

CREATE UNIQUE INDEX ux_payment_company_idempotency ON payments.payment USING btree (company_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);


--
-- Name: ux_payment_provider_token; Type: INDEX; Schema: payments; Owner: -
--

CREATE UNIQUE INDEX ux_payment_provider_token ON payments.payment USING btree (provider, token) WHERE (token IS NOT NULL);


--
-- Name: company trg_company_touch; Type: TRIGGER; Schema: payments; Owner: -
--

CREATE TRIGGER trg_company_touch BEFORE UPDATE ON payments.company FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();


--
-- Name: dispute trg_dispute_touch; Type: TRIGGER; Schema: payments; Owner: -
--

CREATE TRIGGER trg_dispute_touch BEFORE UPDATE ON payments.dispute FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();


--
-- Name: payment trg_payment_initial_state; Type: TRIGGER; Schema: payments; Owner: -
--

CREATE TRIGGER trg_payment_initial_state AFTER INSERT ON payments.payment FOR EACH ROW EXECUTE FUNCTION payments.fn_log_initial_state();


--
-- Name: payment_order trg_payment_order_touch; Type: TRIGGER; Schema: payments; Owner: -
--

CREATE TRIGGER trg_payment_order_touch BEFORE UPDATE ON payments.payment_order FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();


--
-- Name: payment trg_payment_status_change; Type: TRIGGER; Schema: payments; Owner: -
--

CREATE TRIGGER trg_payment_status_change AFTER UPDATE OF status ON payments.payment FOR EACH ROW EXECUTE FUNCTION payments.fn_log_status_change();


--
-- Name: payment trg_payment_touch; Type: TRIGGER; Schema: payments; Owner: -
--

CREATE TRIGGER trg_payment_touch BEFORE UPDATE ON payments.payment FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();


--
-- Name: provider_account trg_provider_account_touch; Type: TRIGGER; Schema: payments; Owner: -
--

CREATE TRIGGER trg_provider_account_touch BEFORE UPDATE ON payments.provider_account FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();


--
-- Name: refund trg_refund_touch; Type: TRIGGER; Schema: payments; Owner: -
--

CREATE TRIGGER trg_refund_touch BEFORE UPDATE ON payments.refund FOR EACH ROW EXECUTE FUNCTION payments.fn_touch_updated_at();


--
-- Name: crm_event_log crm_event_log_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.crm_event_log
    ADD CONSTRAINT crm_event_log_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id);


--
-- Name: crm_push_queue crm_push_queue_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.crm_push_queue
    ADD CONSTRAINT crm_push_queue_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id);


--
-- Name: dispute dispute_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.dispute
    ADD CONSTRAINT dispute_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id) ON DELETE CASCADE;


--
-- Name: payment payment_company_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment
    ADD CONSTRAINT payment_company_id_fkey FOREIGN KEY (company_id) REFERENCES payments.company(id) ON DELETE CASCADE;


--
-- Name: payment_order payment_order_company_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment_order
    ADD CONSTRAINT payment_order_company_id_fkey FOREIGN KEY (company_id) REFERENCES payments.company(id) ON DELETE CASCADE;


--
-- Name: payment payment_payment_order_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment
    ADD CONSTRAINT payment_payment_order_id_fkey FOREIGN KEY (payment_order_id) REFERENCES payments.payment_order(id) ON DELETE CASCADE;


--
-- Name: payment payment_provider_account_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment
    ADD CONSTRAINT payment_provider_account_id_fkey FOREIGN KEY (provider_account_id) REFERENCES payments.provider_account(id);


--
-- Name: payment_state_history payment_state_history_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.payment_state_history
    ADD CONSTRAINT payment_state_history_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id) ON DELETE CASCADE;


--
-- Name: provider_event_log provider_event_log_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.provider_event_log
    ADD CONSTRAINT provider_event_log_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id) ON DELETE SET NULL;


--
-- Name: provider_event_log provider_event_log_provider_account_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.provider_event_log
    ADD CONSTRAINT provider_event_log_provider_account_id_fkey FOREIGN KEY (provider_account_id) REFERENCES payments.provider_account(id) ON DELETE SET NULL;


--
-- Name: refund refund_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.refund
    ADD CONSTRAINT refund_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id) ON DELETE CASCADE;


--
-- Name: settlement_item settlement_item_batch_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.settlement_item
    ADD CONSTRAINT settlement_item_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES payments.settlement_batch(id) ON DELETE CASCADE;


--
-- Name: settlement_item settlement_item_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.settlement_item
    ADD CONSTRAINT settlement_item_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id) ON DELETE SET NULL;


--
-- Name: status_check status_check_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.status_check
    ADD CONSTRAINT status_check_payment_id_fkey FOREIGN KEY (payment_id) REFERENCES payments.payment(id) ON DELETE CASCADE;


--
-- Name: webhook_inbox webhook_inbox_related_payment_id_fkey; Type: FK CONSTRAINT; Schema: payments; Owner: -
--

ALTER TABLE ONLY payments.webhook_inbox
    ADD CONSTRAINT webhook_inbox_related_payment_id_fkey FOREIGN KEY (related_payment_id) REFERENCES payments.payment(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict LXci9fEQhy4JFHDddIHI4oxKuv3aVOTcs6JjkynqA4qnTIp0v0FA5nrnXi09Pzb
