package com.example.prep.partnersend.outbox;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/**
 * One pending message, written to the <em>same database, in the same transaction</em> as
 * the business change it describes.
 *
 * <p>This exists to solve the dual-write problem. The naive code is:
 *
 * <pre>
 *   transferRepository.save(transfer);   // commits
 *   kafka.send(transferCreatedEvent);    // ...and then the process dies
 * </pre>
 *
 * <p>There is no ordering of those two lines that is safe. Save-then-publish loses the
 * event if the process dies in between; publish-then-save emits an event for a transfer
 * that does not exist. They are two separate systems with two separate failure modes, and
 * no amount of try/catch makes them one atomic step. (Distributed transactions / XA do,
 * technically, and are avoided in practice: they need every participant to support
 * two-phase commit, they hold locks across a network round trip, and a coordinator crash
 * leaves rows locked in doubt.)
 *
 * <p>The outbox sidesteps it: the event row and the transfer row are one local ACID
 * transaction. Either both are there or neither is. A separate poller then moves rows from
 * the table to the broker, which gives <b>at-least-once</b> delivery — the poller can
 * publish and die before marking the row sent, and will publish again. Consumers must
 * therefore be idempotent, which is why {@link #eventId} exists as a de-duplication handle.
 */
@Entity
@Table(name = "outbox_event")
public class OutboxEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "sequence_no")
    private Long sequenceNo;

    /** Stable identity of this event, for consumer-side de-duplication. */
    @Column(name = "event_id", nullable = false, updatable = false, unique = true, length = 36)
    private String eventId;

    /** Aggregate the event is about. Doubles as the partition key, which preserves
     *  per-transfer ordering on the broker without needing global ordering. */
    @Column(name = "aggregate_id", nullable = false, updatable = false, length = 36)
    private String aggregateId;

    @Column(name = "event_type", nullable = false, updatable = false, length = 64)
    private String eventType;

    @Column(name = "payload", nullable = false, updatable = false, length = 4000)
    private String payload;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "published_at")
    private Instant publishedAt;

    @Column(name = "attempts", nullable = false)
    private int attempts;

    protected OutboxEvent() {
    }

    private OutboxEvent(String aggregateId, String eventType, String payload, Instant now) {
        this.eventId = UUID.randomUUID().toString();
        this.aggregateId = aggregateId;
        this.eventType = eventType;
        this.payload = payload;
        this.createdAt = now;
        this.attempts = 0;
    }

    public static OutboxEvent of(String aggregateId, String eventType, String payload, Instant now) {
        return new OutboxEvent(aggregateId, eventType, payload, now);
    }

    public void markPublished(Instant now) {
        this.publishedAt = now;
    }

    public void recordAttempt() {
        this.attempts++;
    }

    public Long getSequenceNo() {
        return sequenceNo;
    }

    public String getEventId() {
        return eventId;
    }

    public String getAggregateId() {
        return aggregateId;
    }

    public String getEventType() {
        return eventType;
    }

    public String getPayload() {
        return payload;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getPublishedAt() {
        return publishedAt;
    }

    public boolean isPublished() {
        return publishedAt != null;
    }

    public int getAttempts() {
        return attempts;
    }
}
