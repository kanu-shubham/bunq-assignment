---
title: Runbook - Kafka consumer lag
source: confluence
space: engineering
team: platform
doc_type: runbook
updated: 2026-05-20
tags: [runbook, kafka, lag, consumers, throughput]
---

# Runbook: Kafka consumer lag

**Alert:** `KafkaConsumerLagHigh` — lag above 100k messages for ten minutes on
any consumer group in the `prod` cluster.

## Triage

    kafka-consumer-groups.sh --bootstrap-server $BROKERS --describe --group <group>

Look at whether lag is spread across partitions or concentrated in one. Even
lag across all partitions means the consumer is simply too slow: check pod CPU
and consider scaling replicas up to the partition count. Lag on a single
partition almost always means a poison message or a hot key.

## Common causes

* **Rebalance storm.** Repeated `Attempt to heartbeat failed` in the logs means
  processing exceeds `max.poll.interval.ms`. Reduce `max.poll.records` rather
  than raising the interval.
* **Poison message.** A message that always throws will be retried forever.
  Confirm the offset from the error log and, once the payload has been captured
  for analysis, skip it with `--reset-offsets --to-offset <n+1> --execute`.
* **Downstream slowness.** The consumer is fine, the database it writes to is
  not. Check that first; scaling consumers into a saturated database makes it
  worse, not better.

## Never do this

Do not delete a consumer group to "reset" it. That loses committed offsets for
every partition and replays the entire retention window, which for the payments
topics is seven days of traffic.
