package com.paykit.webhook.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import jakarta.persistence.Version;

import java.time.Duration;
import java.time.Instant;

/**
 * One attempt-tracked delivery of one event to one endpoint.
 *
 * <h3>The retry schedule</h3>
 * Exponential backoff with a cap, plus jitter:
 * {@code 10s, 20s, 40s, 80s, 160s, 320s, 640s, 1280s (capped at 1h)}.
 *
 * <p>Two details that matter more than the numbers:
 * <ul>
 *   <li><b>Jitter.</b> If a merchant's server goes down while 10,000 deliveries are pending,
 *       an un-jittered schedule sends all 10,000 retries at the same instant — repeatedly.
 *       That is a self-inflicted DDoS on a server that is already struggling. Randomising each
 *       delay spreads the load and lets it recover.</li>
 *   <li><b>A cap.</b> Pure doubling reaches days between attempts, by which point the retry is
 *       useless to the merchant anyway.</li>
 * </ul>
 */
@Entity
@Table(name = "webhook_deliveries",
        uniqueConstraints = @UniqueConstraint(
                name = "uq_delivery_event_endpoint", columnNames = {"event_id", "endpoint_id"}),
        indexes = {
                @Index(name = "idx_delivery_due", columnList = "status, next_attempt_at"),
                @Index(name = "idx_delivery_merchant", columnList = "merchant_id, created_at DESC")
        })
public class WebhookDelivery {

    public enum Status {
        PENDING,
        SUCCEEDED,
        /** Retries exhausted or the endpoint was disabled. Needs a manual replay. */
        FAILED
    }

    private static final Duration BASE_DELAY = Duration.ofSeconds(10);
    private static final Duration MAX_DELAY = Duration.ofHours(1);

    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "endpoint_id", nullable = false, length = 64)
    private String endpointId;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    /**
     * Part of a unique constraint with {@code endpoint_id}. Kafka redelivery would otherwise
     * create a second delivery row for the same event, and the merchant would be notified twice
     * about one payment.
     */
    @Column(name = "event_id", nullable = false, length = 64)
    private String eventId;

    @Column(name = "event_type", nullable = false, length = 64)
    private String eventType;

    @Column(nullable = false, columnDefinition = "text")
    private String payload;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private Status status;

    @Column(nullable = false)
    private int attempts;

    @Column(name = "max_attempts", nullable = false)
    private int maxAttempts;

    @Column(name = "next_attempt_at")
    private Instant nextAttemptAt;

    @Column(name = "response_status")
    private Integer responseStatus;

    @Column(name = "last_error", length = 500)
    private String lastError;

    @Column(name = "delivered_at")
    private Instant deliveredAt;

    @Version
    private Long version;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected WebhookDelivery() {
    }

    public WebhookDelivery(String id, String endpointId, String merchantId, String eventId,
                           String eventType, String payload, int maxAttempts) {
        this.id = id;
        this.endpointId = endpointId;
        this.merchantId = merchantId;
        this.eventId = eventId;
        this.eventType = eventType;
        this.payload = payload;
        this.status = Status.PENDING;
        this.attempts = 0;
        this.maxAttempts = maxAttempts;
        this.nextAttemptAt = Instant.now();
        this.createdAt = Instant.now();
    }

    public void markSucceeded(int responseStatus) {
        this.status = Status.SUCCEEDED;
        this.responseStatus = responseStatus;
        this.deliveredAt = Instant.now();
        this.nextAttemptAt = null;
        this.lastError = null;
        this.attempts++;
    }

    /** Records a failure and schedules the next attempt, or gives up. */
    public void markAttemptFailed(Integer responseStatus, String error, double jitterFactor) {
        this.attempts++;
        this.responseStatus = responseStatus;
        this.lastError = error == null ? null : error.substring(0, Math.min(error.length(), 500));

        if (attempts >= maxAttempts) {
            this.status = Status.FAILED;
            this.nextAttemptAt = null;
        } else {
            this.nextAttemptAt = Instant.now().plus(backoffFor(attempts, jitterFactor));
        }
    }

    /** Exponential growth, capped, then spread by up to ±50%. */
    public static Duration backoffFor(int attempt, double jitterFactor) {
        long millis = BASE_DELAY.toMillis() * (long) Math.pow(2, Math.max(0, attempt - 1));
        millis = Math.min(millis, MAX_DELAY.toMillis());
        long jittered = (long) (millis * (0.5 + jitterFactor));
        return Duration.ofMillis(Math.max(jittered, 1_000L));
    }

    public void resetForReplay() {
        this.status = Status.PENDING;
        this.attempts = 0;
        this.nextAttemptAt = Instant.now();
        this.lastError = null;
    }

    public boolean belongsTo(String candidateMerchantId) {
        return merchantId.equals(candidateMerchantId);
    }

    public String getId() {
        return id;
    }

    public String getEndpointId() {
        return endpointId;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public String getEventId() {
        return eventId;
    }

    public String getEventType() {
        return eventType;
    }

    public String getPayload() {
        return payload;
    }

    public Status getStatus() {
        return status;
    }

    public int getAttempts() {
        return attempts;
    }

    public int getMaxAttempts() {
        return maxAttempts;
    }

    public Instant getNextAttemptAt() {
        return nextAttemptAt;
    }

    public Integer getResponseStatus() {
        return responseStatus;
    }

    public String getLastError() {
        return lastError;
    }

    public Instant getDeliveredAt() {
        return deliveredAt;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
