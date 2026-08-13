package com.example.prep.partnersend.idempotency;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.PostLoad;
import jakarta.persistence.PostPersist;
import jakarta.persistence.Table;
import jakarta.persistence.Transient;
import java.time.Instant;
import org.springframework.data.domain.Persistable;

/**
 * One row per (partner, idempotency key). The primary key <em>is</em> the pair, which
 * is what makes the whole scheme work: the uniqueness guarantee is enforced by the
 * database, not by application code checking-then-inserting.
 *
 * <p>A check-then-insert ("does this key exist? no? insert it") is a race, and under
 * a retry storm it is a race you lose regularly. Two requests both read "absent", both
 * insert, and you have paid twice. Here the second INSERT violates the primary key and
 * the loser is told so.
 *
 * <h2>Why this implements {@link Persistable}</h2>
 *
 * <p>This is a JPA trap that silently destroys the guarantee above, and it is worth being
 * able to describe.
 *
 * <p>Spring Data's {@code save()} has to decide between {@code persist()} (INSERT) and
 * {@code merge()} (SELECT, then INSERT or UPDATE). By default it decides by asking whether
 * the {@code @Id} is null. That works for generated ids — but this entity <em>assigns</em>
 * its own id, so the id is never null, {@code save()} concludes the entity is detached, and
 * calls {@code merge()}.
 *
 * <p>{@code merge()} on a key that already exists is an <b>UPDATE</b>. No constraint is
 * violated, no exception is thrown, and the second caller cheerfully overwrites the first
 * caller's claim and proceeds — which is exactly the double payment the primary key was
 * supposed to prevent. The failure is silent, and it only appears under concurrency.
 *
 * <p>Implementing {@link Persistable} takes the decision back: {@code isNew()} returns true
 * until the row has actually been persisted or loaded, so {@code save()} calls
 * {@code persist()} and a duplicate key fails loudly, as intended.
 */
@Entity
@Table(name = "idempotency_record")
public class IdempotencyRecord implements Persistable<String> {

    public enum State {
        /** Claimed by an in-flight request. Nobody else may proceed. */
        IN_PROGRESS,
        /** Finished. The stored response is replayable forever. */
        COMPLETED
    }

    /** Composite natural key, flattened to "partnerId:key" so it stays a simple PK. */
    @Id
    @Column(name = "id", nullable = false, updatable = false, length = 200)
    private String id;

    /**
     * SHA-256 of the canonical request body. Guards against a client reusing one key
     * for two genuinely different payments — which is a client bug we must surface,
     * not silently answer with the wrong stored response.
     */
    @Column(name = "request_hash", nullable = false, updatable = false, length = 64)
    private String requestHash;

    @Enumerated(EnumType.STRING)
    @Column(name = "state", nullable = false, length = 16)
    private State state;

    @Column(name = "response_status")
    private Integer responseStatus;

    @Column(name = "response_body", length = 4000)
    private String responseBody;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    /**
     * Not a column — it exists only to answer {@link #isNew()}. False until JPA tells us
     * the row is real, via either callback below.
     */
    @Transient
    private boolean persisted = false;

    @PostPersist
    @PostLoad
    void markPersisted() {
        this.persisted = true;
    }

    @Override
    public boolean isNew() {
        return !persisted;
    }

    protected IdempotencyRecord() {
    }

    private IdempotencyRecord(String id, String requestHash, Instant now) {
        this.id = id;
        this.requestHash = requestHash;
        this.state = State.IN_PROGRESS;
        this.createdAt = now;
    }

    public static String key(String partnerId, String idempotencyKey) {
        return partnerId + ":" + idempotencyKey;
    }

    public static IdempotencyRecord claim(String partnerId, String idempotencyKey, String requestHash, Instant now) {
        return new IdempotencyRecord(key(partnerId, idempotencyKey), requestHash, now);
    }

    public void complete(int status, String body) {
        this.state = State.COMPLETED;
        this.responseStatus = status;
        this.responseBody = body;
    }

    public boolean matches(String candidateHash) {
        return requestHash.equals(candidateHash);
    }

    @Override
    public String getId() {
        return id;
    }

    public String getRequestHash() {
        return requestHash;
    }

    public State getState() {
        return state;
    }

    public Integer getResponseStatus() {
        return responseStatus;
    }

    public String getResponseBody() {
        return responseBody;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
