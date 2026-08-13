package com.example.prep.partnersend.consumer;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.time.Instant;
import org.springframework.data.domain.Persistable;

/**
 * The consumer-side de-duplication record: one row per event id we have already applied.
 *
 * <p>This is the other half of the delivery-semantics story. The outbox gives at-least-once
 * delivery, which means duplicates are not an edge case — they are guaranteed to happen
 * eventually. This table is what turns at-least-once delivery into exactly-once <b>effect</b>.
 *
 * <p>Same primary-key trick, and for the same reason, as the idempotency record: the
 * uniqueness is enforced by the database rather than by an {@code existsById} check, because
 * with several consumer instances a check-then-act is a race. And the same
 * {@link Persistable} workaround, because the id is assigned rather than generated — see the
 * comment on {@code IdempotencyRecord} for why that matters.
 */
@Entity
@Table(name = "processed_event")
public class ProcessedEvent implements Persistable<String> {

    @Id
    @Column(name = "event_id", nullable = false, updatable = false, length = 64)
    private String eventId;

    @Column(name = "processed_at", nullable = false, updatable = false)
    private Instant processedAt;

    @Transient
    private boolean persisted = false;

    protected ProcessedEvent() {
    }

    public ProcessedEvent(String eventId, Instant processedAt) {
        this.eventId = eventId;
        this.processedAt = processedAt;
    }

    @PostPersist
    @PostLoad
    void markPersisted() {
        this.persisted = true;
    }

    @Override
    public String getId() {
        return eventId;
    }

    @Override
    public boolean isNew() {
        return !persisted;
    }

    public Instant getProcessedAt() {
        return processedAt;
    }
}
