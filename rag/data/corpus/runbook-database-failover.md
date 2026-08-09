---
title: Runbook - PostgreSQL failover
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-04-02
tags: [runbook, postgres, failover, database, patroni]
---

# Runbook: PostgreSQL failover

Our clusters run Patroni with three nodes: one primary and two synchronous
standbys spread across availability zones.

## Automatic failover

Patroni promotes a standby when the primary misses its TTL. Expected impact is
15 to 25 seconds of write unavailability. Reads from the replica pool continue
throughout. Applications must retry on `57P01` (admin shutdown) and on
connection reset; the shared `pgxpool` wrapper already does this with a 5 second
budget.

## Manual failover

Use it before planned maintenance:

    patronictl -c /etc/patroni.yml switchover --master pg-prod-1 --candidate pg-prod-2

Switchover is graceful — it waits for the candidate to catch up. `failover` is
the violent version and can lose the last unreplicated transactions; only use it
when the primary is already gone.

## After a failover

1. Confirm the new topology: `patronictl list`.
2. Check replication lag on both standbys. Anything above 10 MB sustained needs
   investigation before you go back to bed.
3. Verify connection pools have reconnected — `pgbouncer_pools_server_active`
   should return to baseline within a minute.
4. Open a follow-up ticket to re-seed the demoted node if it did not rejoin
   automatically. A two-node cluster is not a resilient cluster.
