-- ============================================================================
-- Flyway migration 1 — merchants and API keys.
--
-- WHY MIGRATIONS AND NOT hibernate.ddl-auto=update:
--   * ddl-auto guesses. It will happily add a column and never drop one, never
--     backfill data, and never write an index you did not model.
--   * Migrations are ordered, versioned and reviewed in the same pull request as
--     the code that needs them, and Flyway records which have run, so every
--     environment converges on the same schema.
--   * They run identically in CI, in Testcontainers and in production, which is
--     what makes "it worked on my machine" a solvable problem.
-- ============================================================================

CREATE TABLE merchants (
    id           VARCHAR(64)  PRIMARY KEY,
    name         VARCHAR(200) NOT NULL,
    email        VARCHAR(255) NOT NULL,
    country_code CHAR(2)      NOT NULL,
    status       VARCHAR(32)  NOT NULL,
    version      BIGINT       NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ  NOT NULL,
    updated_at   TIMESTAMPTZ  NOT NULL,

    CONSTRAINT merchants_email_unique UNIQUE (email),
    CONSTRAINT merchants_status_valid
        CHECK (status IN ('PENDING', 'ACTIVE', 'SUSPENDED', 'CLOSED'))
);

-- A CHECK constraint is the last line of defence. Application validation can be
-- bypassed by a migration, a script or a bug; the database cannot be talked out of it.

CREATE TABLE api_keys (
    id           VARCHAR(64)  PRIMARY KEY,
    merchant_id  VARCHAR(64)  NOT NULL,
    key_prefix   VARCHAR(32)  NOT NULL,
    secret_hash  VARCHAR(255) NOT NULL,
    livemode     BOOLEAN      NOT NULL DEFAULT FALSE,
    revoked      BOOLEAN      NOT NULL DEFAULT FALSE,
    scopes       VARCHAR(512) NOT NULL,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL,

    CONSTRAINT api_keys_prefix_unique UNIQUE (key_prefix),
    CONSTRAINT api_keys_merchant_fk FOREIGN KEY (merchant_id)
        REFERENCES merchants (id) ON DELETE CASCADE
);

-- The hot path: every API-key verification looks a key up by its prefix. Without
-- this index that is a sequential scan of the whole table on every request.
-- (The UNIQUE constraint above already creates one; this is here to make the
-- access pattern explicit for anyone reading the schema.)
CREATE INDEX idx_api_keys_merchant ON api_keys (merchant_id, created_at DESC);

-- Partial index: we only ever list keys that are still usable, and in a mature
-- account most rows are revoked. Indexing just the live ones keeps it small.
CREATE INDEX idx_api_keys_active ON api_keys (merchant_id) WHERE revoked = FALSE;
