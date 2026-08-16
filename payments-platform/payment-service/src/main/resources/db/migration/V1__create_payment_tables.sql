-- ============================================================================
-- Flyway migration 1 — payments, charges, refunds, idempotency, outbox.
-- ============================================================================

CREATE TABLE payment_intents (
    id                VARCHAR(64)  PRIMARY KEY,
    merchant_id       VARCHAR(64)  NOT NULL,
    amount_minor      BIGINT       NOT NULL,
    currency          VARCHAR(3)   NOT NULL,
    status            VARCHAR(32)  NOT NULL,
    customer_id       VARCHAR(64),
    payment_method_id VARCHAR(64),
    description       VARCHAR(500),
    -- jsonb, not text: it is parsed once on write, stored in a binary form, and can be
    -- indexed and queried (metadata->>'order_id'). 'json' would keep the raw string and
    -- re-parse on every read.
    metadata          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    charge_id         VARCHAR(64),
    failure_code      VARCHAR(64),
    failure_message   VARCHAR(500),
    attempt_count     INT          NOT NULL DEFAULT 0,
    confirmed_at      TIMESTAMPTZ,
    succeeded_at      TIMESTAMPTZ,
    canceled_at       TIMESTAMPTZ,
    version           BIGINT       NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ  NOT NULL,
    updated_at        TIMESTAMPTZ  NOT NULL,

    -- Money is a positive integer count of minor units. The database refuses anything else,
    -- whatever the application layer believes.
    CONSTRAINT pi_amount_positive CHECK (amount_minor > 0),
    CONSTRAINT pi_status_valid CHECK (
        status IN ('REQUIRES_CONFIRMATION', 'PROCESSING', 'SUCCEEDED', 'FAILED', 'CANCELED')),
    -- A succeeded payment must have a charge. An invariant the code maintains, and the
    -- database now guarantees even if the code stops.
    CONSTRAINT pi_succeeded_has_charge CHECK (status <> 'SUCCEEDED' OR charge_id IS NOT NULL)
);

-- The list endpoint's exact access pattern: filter by merchant, sort by recency.
-- A composite index whose column order matches the query is the difference between an
-- index scan and reading the whole table.
CREATE INDEX idx_pi_merchant_created ON payment_intents (merchant_id, created_at DESC);
CREATE INDEX idx_pi_status ON payment_intents (status);

-- Partial index for the reconciliation job. PROCESSING rows are a tiny fraction of the
-- table, so indexing only those keeps the index small enough to stay in memory.
CREATE INDEX idx_pi_processing ON payment_intents (confirmed_at)
    WHERE status = 'PROCESSING';

-- GIN index over jsonb, so a merchant can look a payment up by their own order id.
CREATE INDEX idx_pi_metadata ON payment_intents USING GIN (metadata);

CREATE TABLE charges (
    id                 VARCHAR(64) PRIMARY KEY,
    payment_intent_id  VARCHAR(64) NOT NULL,
    merchant_id        VARCHAR(64) NOT NULL,
    amount_minor       BIGINT      NOT NULL,
    fee_minor          BIGINT      NOT NULL,
    refunded_minor     BIGINT      NOT NULL DEFAULT 0,
    currency           VARCHAR(3)  NOT NULL,
    card_brand         VARCHAR(32),
    card_last4         CHAR(4),
    acquirer_reference VARCHAR(128),
    payment_method_id  VARCHAR(64),
    version            BIGINT      NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL,

    CONSTRAINT charges_payment_intent_fk FOREIGN KEY (payment_intent_id)
        REFERENCES payment_intents (id),
    -- One charge per payment intent: this is what makes a double charge structurally
    -- impossible rather than merely unlikely.
    CONSTRAINT charges_one_per_intent UNIQUE (payment_intent_id),
    CONSTRAINT charges_amount_positive CHECK (amount_minor > 0),
    CONSTRAINT charges_fee_not_negative CHECK (fee_minor >= 0),
    -- The core refund invariant, enforced by the database.
    CONSTRAINT charges_refund_within_bounds
        CHECK (refunded_minor >= 0 AND refunded_minor <= amount_minor)
);

CREATE INDEX idx_charge_merchant_created ON charges (merchant_id, created_at DESC);
CREATE INDEX idx_charge_payment_intent ON charges (payment_intent_id);

CREATE TABLE refunds (
    id                 VARCHAR(64) PRIMARY KEY,
    charge_id          VARCHAR(64) NOT NULL,
    payment_intent_id  VARCHAR(64) NOT NULL,
    merchant_id        VARCHAR(64) NOT NULL,
    amount_minor       BIGINT      NOT NULL,
    fee_refunded_minor BIGINT      NOT NULL DEFAULT 0,
    currency           VARCHAR(3)  NOT NULL,
    reason             VARCHAR(32) NOT NULL,
    status             VARCHAR(32) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL,

    CONSTRAINT refunds_charge_fk FOREIGN KEY (charge_id) REFERENCES charges (id),
    CONSTRAINT refunds_amount_positive CHECK (amount_minor > 0),
    CONSTRAINT refunds_status_valid CHECK (status IN ('PENDING', 'SUCCEEDED', 'FAILED'))
);

CREATE INDEX idx_refund_charge ON refunds (charge_id);
CREATE INDEX idx_refund_merchant_created ON refunds (merchant_id, created_at DESC);

CREATE TABLE idempotency_records (
    -- merchant_id + ':' + key. Scoping to the tenant is mandatory: a global key space
    -- would let one merchant's retry replay another merchant's response.
    scoped_key      VARCHAR(200) PRIMARY KEY,
    merchant_id     VARCHAR(64)  NOT NULL,
    request_hash    VARCHAR(64)  NOT NULL,
    endpoint        VARCHAR(200) NOT NULL,
    state           VARCHAR(16)  NOT NULL,
    response_status INT,
    response_body   TEXT,
    resource_id     VARCHAR(64),
    version         BIGINT       NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL,

    CONSTRAINT idem_state_valid CHECK (state IN ('IN_PROGRESS', 'COMPLETED')),
    -- A completed record must be able to answer a replay.
    CONSTRAINT idem_completed_has_response
        CHECK (state <> 'COMPLETED' OR response_body IS NOT NULL)
);

-- Supports the retention sweeper, which deletes by age.
CREATE INDEX idx_idem_created ON idempotency_records (created_at);

CREATE TABLE outbox_events (
    id             BIGSERIAL    PRIMARY KEY,
    event_id       VARCHAR(64)  NOT NULL,
    aggregate_id   VARCHAR(64)  NOT NULL,
    merchant_id    VARCHAR(64)  NOT NULL,
    event_type     VARCHAR(64)  NOT NULL,
    payload        TEXT         NOT NULL,
    correlation_id VARCHAR(64),
    created_at     TIMESTAMPTZ  NOT NULL,
    published_at   TIMESTAMPTZ,
    attempts       INT          NOT NULL DEFAULT 0,
    last_error     VARCHAR(500),

    CONSTRAINT outbox_event_id_unique UNIQUE (event_id)
);

-- The publisher polls "published_at IS NULL ORDER BY id" several times a second. A partial
-- index means that query touches only the backlog, which is normally near zero rows, rather
-- than an index covering the entire history of the system.
CREATE INDEX idx_outbox_unpublished ON outbox_events (id) WHERE published_at IS NULL;
CREATE INDEX idx_outbox_published_at ON outbox_events (published_at) WHERE published_at IS NOT NULL;
