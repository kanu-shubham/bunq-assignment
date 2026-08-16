package com.paykit.ledger.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * The consumer's deduplication table: "we have already applied this event".
 *
 * <h3>Why it is necessary</h3>
 * Kafka gives at-least-once delivery, and the transactional outbox can re-send after a crash.
 * So the same {@code payment_intent.succeeded} <em>will</em> arrive twice eventually. Without
 * this table that means the merchant's balance is credited twice — the ledger equivalent of
 * printing money.
 *
 * <h3>Why it works</h3>
 * The event id is the primary key, and the row is inserted <b>in the same transaction</b> as
 * the ledger postings. Either both commit or neither does. A duplicate hits the primary-key
 * constraint, the transaction rolls back, and the consumer acknowledges the message anyway
 * because the work was already done.
 *
 * <p>This is what "exactly-once processing" actually means in practice: at-least-once delivery
 * plus an idempotent consumer. There is no magic broker setting that replaces it.
 */
@Entity
@Table(name = "processed_events")
public class ProcessedEvent {

    @Id
    @Column(name = "event_id", length = 64)
    private String eventId;

    @Column(name = "event_type", nullable = false, length = 64)
    private String eventType;

    @Column(name = "processed_at", nullable = false)
    private Instant processedAt;

    protected ProcessedEvent() {
    }

    public ProcessedEvent(String eventId, String eventType) {
        this.eventId = eventId;
        this.eventType = eventType;
        this.processedAt = Instant.now();
    }

    public String getEventId() {
        return eventId;
    }

    public String getEventType() {
        return eventType;
    }

    public Instant getProcessedAt() {
        return processedAt;
    }
}
