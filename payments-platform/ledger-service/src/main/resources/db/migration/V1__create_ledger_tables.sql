-- ============================================================================
-- Flyway migration 1 — the double-entry ledger.
-- ============================================================================

CREATE TABLE ledger_accounts (
    id            VARCHAR(96) PRIMARY KEY,
    merchant_id   VARCHAR(64) NOT NULL,
    account_type  VARCHAR(40) NOT NULL,
    currency      VARCHAR(3)  NOT NULL,
    -- Cached running balance. The postings in ledger_entries are authoritative; this column
    -- exists so a balance read is O(1) instead of an aggregate over the merchant's history.
    balance_minor BIGINT      NOT NULL DEFAULT 0,
    version       BIGINT      NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL,

    CONSTRAINT uq_ledger_account UNIQUE (merchant_id, account_type, currency),
    CONSTRAINT ledger_account_type_valid CHECK (account_type IN (
        'CARD_NETWORK_CLEARING', 'MERCHANT_PAYABLE', 'PLATFORM_FEE_REVENUE', 'PLATFORM_CASH'))
);

CREATE INDEX idx_account_merchant ON ledger_accounts (merchant_id);

CREATE TABLE ledger_entries (
    id              VARCHAR(64) PRIMARY KEY,
    transaction_id  VARCHAR(64) NOT NULL,
    account_id      VARCHAR(96) NOT NULL,
    merchant_id     VARCHAR(64) NOT NULL,
    direction       VARCHAR(6)  NOT NULL,
    amount_minor    BIGINT      NOT NULL,
    currency        VARCHAR(3)  NOT NULL,
    source_event_id VARCHAR(64) NOT NULL,
    source_type     VARCHAR(64) NOT NULL,
    reference_id    VARCHAR(64),
    created_at      TIMESTAMPTZ NOT NULL,

    CONSTRAINT entry_account_fk FOREIGN KEY (account_id) REFERENCES ledger_accounts (id),
    CONSTRAINT entry_direction_valid CHECK (direction IN ('DEBIT', 'CREDIT')),
    -- A posting is always a positive quantity; the direction carries the sign. Allowing
    -- negative amounts would mean the same movement could be written two ways, and every
    -- report would have to handle both.
    CONSTRAINT entry_amount_positive CHECK (amount_minor > 0)
);

CREATE INDEX idx_entry_account ON ledger_entries (account_id, created_at DESC);
CREATE INDEX idx_entry_transaction ON ledger_entries (transaction_id);
CREATE INDEX idx_entry_merchant ON ledger_entries (merchant_id, created_at DESC);
CREATE INDEX idx_entry_source_event ON ledger_entries (source_event_id);

-- The consumer's idempotency guard. Kafka delivers at least once, so this table is the
-- difference between a correct ledger and one that credits a merchant twice.
CREATE TABLE processed_events (
    event_id     VARCHAR(64) PRIMARY KEY,
    event_type   VARCHAR(64) NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_processed_at ON processed_events (processed_at);
