package com.paykit.payment.domain;

import com.paykit.common.error.Exceptions;
import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EntityListeners;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

/**
 * The central aggregate: one customer's intent to pay a merchant.
 *
 * <h3>A rich entity, not a bag of setters</h3>
 * Every state change goes through a named method ({@link #confirm}, {@link #markSucceeded},
 * {@link #markFailed}, {@link #cancel}) that validates the transition first. There is no
 * {@code setStatus}. That is deliberate: if any caller can write any status, the state machine
 * in {@link PaymentIntentStatus} is decoration rather than a rule.
 *
 * <h3>Money as two columns</h3>
 * {@code Money} is an immutable record, which Hibernate cannot map directly. The entity stores
 * {@code amount_minor} and {@code currency} and reassembles the value object in {@link #getAmount()},
 * so the domain still speaks {@code Money} while the table stays a plain, queryable shape.
 */
@Entity
@Table(name = "payment_intents", indexes = {
        @Index(name = "idx_pi_merchant_created", columnList = "merchant_id, created_at DESC"),
        @Index(name = "idx_pi_status", columnList = "status")
})
@EntityListeners(AuditingEntityListener.class)
public class PaymentIntent {

    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    @Column(name = "amount_minor", nullable = false)
    private long amountMinor;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 3)
    private Currency currency;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private PaymentIntentStatus status;

    @Column(name = "customer_id", length = 64)
    private String customerId;

    @Column(name = "payment_method_id", length = 64)
    private String paymentMethodId;

    @Column(length = 500)
    private String description;

    /**
     * HIBERNATE 6 — {@code @JdbcTypeCode(SqlTypes.JSON)} maps a {@code Map} to a real Postgres
     * {@code jsonb} column. Merchants attach arbitrary keys here (order ids, cart references),
     * and jsonb keeps them queryable and indexable instead of an opaque blob of text.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    private Map<String, String> metadata = new HashMap<>();

    @Column(name = "charge_id", length = 64)
    private String chargeId;

    @Column(name = "failure_code", length = 64)
    private String failureCode;

    @Column(name = "failure_message", length = 500)
    private String failureMessage;

    @Column(name = "attempt_count", nullable = false)
    private int attemptCount;

    @Column(name = "confirmed_at")
    private Instant confirmedAt;

    @Column(name = "succeeded_at")
    private Instant succeededAt;

    @Column(name = "canceled_at")
    private Instant canceledAt;

    /**
     * The concurrency guard. Two confirm requests arriving together both read version 0;
     * the first commits and moves it to 1, the second's UPDATE matches zero rows and fails.
     * Without this, both would charge the card.
     */
    @Version
    private Long version;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @LastModifiedDate
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected PaymentIntent() {
    }

    public PaymentIntent(String id, String merchantId, Money amount, String customerId,
                         String description, Map<String, String> metadata) {
        if (!amount.isPositive()) {
            throw new Exceptions.InvalidRequestException("Amount must be greater than zero", "amount");
        }
        this.id = id;
        this.merchantId = merchantId;
        this.amountMinor = amount.minorUnits();
        this.currency = amount.currency();
        this.customerId = customerId;
        this.description = description;
        this.metadata = metadata == null ? new HashMap<>() : new HashMap<>(metadata);
        this.status = PaymentIntentStatus.REQUIRES_CONFIRMATION;
        this.attemptCount = 0;
    }

    // ---------- state transitions ----------

    /** Moves to PROCESSING and records the attempt. Called before contacting the acquirer. */
    public void confirm(String paymentMethodId) {
        transitionTo(PaymentIntentStatus.PROCESSING);
        this.paymentMethodId = paymentMethodId;
        this.confirmedAt = Instant.now();
        this.attemptCount++;
        this.failureCode = null;
        this.failureMessage = null;
    }

    public void markSucceeded(String chargeId) {
        transitionTo(PaymentIntentStatus.SUCCEEDED);
        this.chargeId = chargeId;
        this.succeededAt = Instant.now();
    }

    public void markFailed(String failureCode, String failureMessage) {
        transitionTo(PaymentIntentStatus.FAILED);
        this.failureCode = failureCode;
        this.failureMessage = failureMessage;
    }

    public void cancel(String reason) {
        transitionTo(PaymentIntentStatus.CANCELED);
        this.canceledAt = Instant.now();
        if (reason != null) {
            this.metadata.put("cancellation_reason", reason);
        }
    }

    /**
     * The single gate every status change passes through. One method to read, one place to
     * change, and an exception that names both ends of the illegal move.
     */
    private void transitionTo(PaymentIntentStatus target) {
        if (!status.canTransitionTo(target)) {
            throw new Exceptions.InvalidStateTransitionException(
                    "PaymentIntent " + id, status.wireValue(), target.wireValue());
        }
        this.status = target;
    }

    // ---------- queries ----------

    public Money getAmount() {
        return Money.of(amountMinor, currency);
    }

    public boolean belongsTo(String candidateMerchantId) {
        return merchantId.equals(candidateMerchantId);
    }

    public String getId() {
        return id;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public PaymentIntentStatus getStatus() {
        return status;
    }

    public String getCustomerId() {
        return customerId;
    }

    public String getPaymentMethodId() {
        return paymentMethodId;
    }

    public String getDescription() {
        return description;
    }

    public Map<String, String> getMetadata() {
        return Map.copyOf(metadata);
    }

    public String getChargeId() {
        return chargeId;
    }

    public String getFailureCode() {
        return failureCode;
    }

    public String getFailureMessage() {
        return failureMessage;
    }

    public int getAttemptCount() {
        return attemptCount;
    }

    public Instant getConfirmedAt() {
        return confirmedAt;
    }

    public Instant getSucceededAt() {
        return succeededAt;
    }

    public Instant getCanceledAt() {
        return canceledAt;
    }

    public Long getVersion() {
        return version;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
