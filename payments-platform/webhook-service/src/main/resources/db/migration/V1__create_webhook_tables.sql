-- ============================================================================
-- Flyway migration 1 — webhook endpoints and delivery attempts.
-- ============================================================================

CREATE TABLE webhook_endpoints (
    id                   VARCHAR(64)   PRIMARY KEY,
    merchant_id          VARCHAR(64)   NOT NULL,
    url                  VARCHAR(500)  NOT NULL,
    -- Stored in plaintext because HMAC signing needs the original value. In production this
    -- belongs in a KMS or an encrypted column; see WebhookEndpoint.secret.
    secret               VARCHAR(128)  NOT NULL,
    enabled_events       VARCHAR(1000) NOT NULL DEFAULT '*',
    status               VARCHAR(16)   NOT NULL,
    consecutive_failures INT           NOT NULL DEFAULT 0,
    description          VARCHAR(200)  NOT NULL DEFAULT '',
    version              BIGINT        NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ   NOT NULL,

    CONSTRAINT endpoint_status_valid CHECK (status IN ('ENABLED', 'DISABLED'))
);

CREATE INDEX idx_endpoint_merchant ON webhook_endpoints (merchant_id);

CREATE TABLE webhook_deliveries (
    id              VARCHAR(64) PRIMARY KEY,
    endpoint_id     VARCHAR(64) NOT NULL,
    merchant_id     VARCHAR(64) NOT NULL,
    event_id        VARCHAR(64) NOT NULL,
    event_type      VARCHAR(64) NOT NULL,
    payload         TEXT        NOT NULL,
    status          VARCHAR(16) NOT NULL,
    attempts        INT         NOT NULL DEFAULT 0,
    max_attempts    INT         NOT NULL,
    next_attempt_at TIMESTAMPTZ,
    response_status INT,
    last_error      VARCHAR(500),
    delivered_at    TIMESTAMPTZ,
    version         BIGINT      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL,

    CONSTRAINT delivery_endpoint_fk FOREIGN KEY (endpoint_id)
        REFERENCES webhook_endpoints (id) ON DELETE CASCADE,
    -- One delivery per (event, endpoint). This is what makes Kafka redelivery harmless:
    -- the merchant is notified once even if we consume the event three times.
    CONSTRAINT uq_delivery_event_endpoint UNIQUE (event_id, endpoint_id),
    CONSTRAINT delivery_status_valid CHECK (status IN ('PENDING', 'SUCCEEDED', 'FAILED'))
);

-- The sender's claim query: "PENDING and due". A partial index keeps it to the small set of
-- rows actually waiting, rather than every delivery ever made.
CREATE INDEX idx_delivery_due ON webhook_deliveries (next_attempt_at)
    WHERE status = 'PENDING';
CREATE INDEX idx_delivery_merchant ON webhook_deliveries (merchant_id, created_at DESC);

CREATE TABLE processed_events (
    event_id     VARCHAR(64) PRIMARY KEY,
    event_type   VARCHAR(64) NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL
);
