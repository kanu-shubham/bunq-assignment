package com.paykit.common.kafka;

/**
 * Kafka topic names, declared once so a typo cannot silently create a new topic.
 *
 * <p>NAMING — {@code <domain>.<entity>.<version>}. The version suffix is the escape hatch
 * for a breaking schema change: publish {@code payments.events.v2} alongside v1, migrate
 * consumers one at a time, then retire v1.
 */
public final class Topics {

    private Topics() {
        throw new AssertionError("No instances");
    }

    /** Every payment lifecycle event. Partitioned by payment-intent id to preserve per-payment ordering. */
    public static final String PAYMENT_EVENTS = "payments.events.v1";

    /** Consumed by the ledger to write double-entry postings. */
    public static final String LEDGER_POSTINGS = "ledger.postings.v1";

    /** Webhook deliveries that exhausted their retries; a human or a replay job drains this. */
    public static final String WEBHOOK_DLQ = "webhooks.dlq.v1";

    /** Payment events that could not be consumed after retries. */
    public static final String PAYMENT_EVENTS_DLQ = "payments.events.dlq.v1";

    public static final String LEDGER_GROUP = "ledger-service";
    public static final String WEBHOOK_GROUP = "webhook-service";
}
