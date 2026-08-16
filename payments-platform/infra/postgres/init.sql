-- ============================================================================
-- One database per service.
--
-- WHY NOT ONE SHARED DATABASE
--   A shared schema is the most common way a "microservice" architecture quietly becomes a
--   distributed monolith: two services join each other's tables, and now neither can change
--   its schema, deploy independently, or be scaled separately. Separate databases make that
--   coupling impossible rather than merely discouraged. Services integrate through APIs and
--   Kafka events, which are contracts you can version.
--
--   The cost is real: no cross-service joins, no cross-service transactions. That is why the
--   platform uses the outbox pattern and eventual consistency instead — the price of
--   independence, paid deliberately.
--
--   One Postgres *instance* hosting several databases is a development convenience. In
--   production each would be its own instance or cluster, so one service's load or one bad
--   query cannot affect the others.
-- ============================================================================

CREATE DATABASE paykit_auth;
CREATE DATABASE paykit_payments;
CREATE DATABASE paykit_ledger;
CREATE DATABASE paykit_webhooks;

GRANT ALL PRIVILEGES ON DATABASE paykit_auth     TO paykit;
GRANT ALL PRIVILEGES ON DATABASE paykit_payments TO paykit;
GRANT ALL PRIVILEGES ON DATABASE paykit_ledger   TO paykit;
GRANT ALL PRIVILEGES ON DATABASE paykit_webhooks TO paykit;
