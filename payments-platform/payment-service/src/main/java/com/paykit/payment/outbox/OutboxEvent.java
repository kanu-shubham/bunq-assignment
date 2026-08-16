package com.paykit.payment.outbox;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * One row per event that must reach Kafka. The heart of the <b>transactional outbox</b>.
 *
 * <h3>The dual-write problem</h3>
 * A payment succeeds. Two things must happen: the row is updated, and an event is published so
 * the ledger and webhooks know. Doing them separately has no good ordering:
 *
 * <pre>
 *   save() ; kafka.send()   →  crash in between: money moved, ledger never hears. Books wrong.
 *   kafka.send() ; save()   →  crash in between: ledger records a payment that never happened.
 * </pre>
 *
 * A distributed transaction across Postgres and Kafka would solve it, and is both unsupported
 * here and a performance disaster where it exists.
 *
 * <h3>The outbox</h3>
 * Write the event into <em>the same database, in the same transaction</em> as the business
 * change. One commit, so they are atomic by construction. A separate poller then reads
 * unpublished rows and sends them to Kafka, marking each one sent.
 *
 * <p>The guarantee is <b>at-least-once</b>: a crash between "sent to Kafka" and "marked
 * published" causes a re-send. That is why every consumer in this platform deduplicates on
 * {@code eventId}. Exactly-once delivery is not available; exactly-once <em>effect</em>,
 * via at-least-once delivery plus idempotent consumers, is.
 */
@Entity
@Table(name = "outbox_events", indexes = {
        @Index(name = "idx_outbox_unpublished", columnList = "published_at, id")
})
public class OutboxEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "event_id", nullable = false, unique = true, length = 64)
    private String eventId;

    @Column(name = "aggregate_id", nullable = false, length = 64)
    private String aggregateId;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    @Column(name = "event_type", nullable = false, length = 64)
    private String eventType;

    @Column(nullable = false, columnDefinition = "text")
    private String payload;

    @Column(name = "correlation_id", length = 64)
    private String correlationId;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    /** NULL means "still waiting to be published". This is what the poller selects on. */
    @Column(name = "published_at")
    private Instant publishedAt;

    @Column(name = "attempts", nullable = false)
    private int attempts;

    @Column(name = "last_error", length = 500)
    private String lastError;

    protected OutboxEvent() {
    }

    public OutboxEvent(String eventId, String aggregateId, String merchantId, String eventType,
                       String payload, String correlationId) {
        this.eventId = eventId;
        this.aggregateId = aggregateId;
        this.merchantId = merchantId;
        this.eventType = eventType;
        this.payload = payload;
        this.correlationId = correlationId;
        this.createdAt = Instant.now();
        this.attempts = 0;
    }

    public void markPublished() {
        this.publishedAt = Instant.now();
        this.lastError = null;
    }

    public void markFailed(String error) {
        this.attempts++;
        // Truncate: an error message must never be the reason a write fails.
        this.lastError = error == null ? null : error.substring(0, Math.min(error.length(), 500));
    }

    public boolean isPublished() {
        return publishedAt != null;
    }

    public Long getId() {
        return id;
    }

    public String getEventId() {
        return eventId;
    }

    public String getAggregateId() {
        return aggregateId;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public String getEventType() {
        return eventType;
    }

    public String getPayload() {
        return payload;
    }

    public String getCorrelationId() {
        return correlationId;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getPublishedAt() {
        return publishedAt;
    }

    public int getAttempts() {
        return attempts;
    }

    public String getLastError() {
        return lastError;
    }
}
