package com.paykit.payment.idempotency;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

import java.time.Instant;

/**
 * The durable memory of "we already did this".
 *
 * <h3>The problem</h3>
 * A client sends {@code POST /v1/payment_intents}. The payment is created, and then the
 * connection drops before the response arrives. The client has no idea whether it worked, so
 * it retries. Without protection, the customer is charged twice — and the client was doing
 * exactly the right thing.
 *
 * <h3>The contract</h3>
 * The client sends {@code Idempotency-Key: <uuid>} with the request and reuses that key on
 * every retry. The server then guarantees:
 * <ul>
 *   <li>Same key, same request body → the <em>stored original response</em> is replayed.
 *       The side effect happens exactly once.</li>
 *   <li>Same key, <em>different</em> body → 409. The key has been reused for a different
 *       operation, which is a client bug, and silently doing one or the other would be worse.</li>
 * </ul>
 *
 * <p>The body hash is what makes the second rule enforceable, and it is stored rather than
 * the body itself: request bodies contain payment details we have no reason to keep.
 *
 * <h3>Why the database, not just Redis</h3>
 * Redis is the lock (fast, short-lived); Postgres is the record (durable). A Redis flush must
 * not turn into a double charge, and the unique constraint on the key is the final guarantee
 * even if two requests somehow slip past the lock.
 */
@Entity
@Table(name = "idempotency_records", indexes = {
        @Index(name = "idx_idem_created", columnList = "created_at")
})
public class IdempotencyRecord {

    /**
     * A reservation is written <em>before</em> the operation runs and completed afterwards.
     *
     * <p>That ordering is what makes the unique constraint on {@code scoped_key} do the real
     * work: two concurrent retries race to INSERT, exactly one wins, and the loser gets a
     * constraint violation instead of a second charge. Writing the record only on success
     * would leave the window between "charge the card" and "remember that we did" wide open.
     */
    public enum State {
        /** Reserved, operation running. A crash can leave a row here — see the sweeper. */
        IN_PROGRESS,
        /** Finished; {@code responseBody} holds the original response to replay. */
        COMPLETED
    }

    /**
     * The primary key is {@code merchantId + ':' + key}, so two merchants can independently
     * use the key "1" without colliding. Scoping idempotency keys to the tenant is not
     * optional — global keys would leak one merchant's response to another.
     */
    @Id
    @Column(name = "scoped_key", length = 200)
    private String scopedKey;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    @Column(name = "request_hash", nullable = false, length = 64)
    private String requestHash;

    @Column(name = "endpoint", nullable = false, length = 200)
    private String endpoint;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private State state;

    @Column(name = "response_status")
    private Integer responseStatus;

    @Column(name = "response_body", columnDefinition = "text")
    private String responseBody;

    @Version
    private Long version;

    @Column(name = "resource_id", length = 64)
    private String resourceId;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected IdempotencyRecord() {
    }

    /** Creates the IN_PROGRESS reservation. */
    public IdempotencyRecord(String merchantId, String key, String requestHash, String endpoint) {
        this.scopedKey = scopedKey(merchantId, key);
        this.merchantId = merchantId;
        this.requestHash = requestHash;
        this.endpoint = endpoint;
        this.state = State.IN_PROGRESS;
        this.createdAt = Instant.now();
    }

    /** Stores the response so an identical retry can be answered without re-executing. */
    public void complete(int responseStatus, String responseBody, String resourceId) {
        this.state = State.COMPLETED;
        this.responseStatus = responseStatus;
        this.responseBody = responseBody;
        this.resourceId = resourceId;
    }

    public boolean isCompleted() {
        return state == State.COMPLETED;
    }

    public static String scopedKey(String merchantId, String key) {
        return merchantId + ":" + key;
    }

    public boolean matches(String candidateHash) {
        return requestHash.equals(candidateHash);
    }

    public String getScopedKey() {
        return scopedKey;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public String getRequestHash() {
        return requestHash;
    }

    public String getEndpoint() {
        return endpoint;
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

    public String getResourceId() {
        return resourceId;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Long getVersion() {
        return version;
    }
}
