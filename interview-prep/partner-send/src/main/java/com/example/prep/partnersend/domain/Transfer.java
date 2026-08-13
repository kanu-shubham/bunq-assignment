package com.example.prep.partnersend.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.Instant;
import java.util.Currency;
import java.util.UUID;

/**
 * The transfer aggregate. State only ever changes through {@link #transitionTo},
 * so there is exactly one place where an illegal lifecycle move can be caught.
 *
 * <p>Note the {@code @Version} field. It gives us <b>optimistic locking</b>: Hibernate
 * appends {@code WHERE version = ?} to every UPDATE and bumps the number. If two
 * workers load the same transfer and both try to write, the second one's UPDATE
 * matches zero rows and Hibernate throws {@code OptimisticLockException}. That is
 * the cheap, non-blocking way to stop a webhook and a reconciliation job from
 * concurrently deciding the same payment both settled and failed — no row locks,
 * no deadlocks, and the loser simply retries against fresh state.
 */
@Entity
@Table(name = "transfer")
public class Transfer {

    @Id
    @Column(name = "id", nullable = false, updatable = false, length = 36)
    private String id;

    @Column(name = "partner_id", nullable = false, updatable = false, length = 64)
    private String partnerId;

    /** The partner's own identifier for this payment — used when we talk back to them. */
    @Column(name = "partner_reference", nullable = false, updatable = false, length = 128)
    private String partnerReference;

    @Column(name = "amount_minor_units", nullable = false, updatable = false)
    private long amountMinorUnits;

    @Column(name = "currency_code", nullable = false, updatable = false, length = 3)
    private String currencyCode;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 32)
    private TransferStatus status;

    /** Set when the partner bank acknowledges — our handle on their record of it. */
    @Column(name = "scheme_reference", length = 128)
    private String schemeReference;

    @Column(name = "failure_reason", length = 256)
    private String failureReason;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    @Version
    @Column(name = "version", nullable = false)
    private long version;

    /** JPA requires a no-arg constructor. Not for application use. */
    protected Transfer() {
    }

    private Transfer(String id, String partnerId, String partnerReference, Money amount, Instant now) {
        this.id = id;
        this.partnerId = partnerId;
        this.partnerReference = partnerReference;
        this.amountMinorUnits = amount.minorUnits();
        this.currencyCode = amount.currency().getCurrencyCode();
        this.status = TransferStatus.RECEIVED;
        this.createdAt = now;
        this.updatedAt = now;
    }

    public static Transfer receive(String partnerId, String partnerReference, Money amount, Instant now) {
        if (!amount.isPositive()) {
            throw new IllegalArgumentException("Transfer amount must be positive, got " + amount);
        }
        return new Transfer(UUID.randomUUID().toString(), partnerId, partnerReference, amount, now);
    }

    /**
     * The only legal way to change status. Rejects moves the state machine does not
     * allow, which turns a class of ordering bugs (a late webhook trying to settle
     * an already-returned payment) into a loud, testable exception.
     */
    public void transitionTo(TransferStatus next, Instant now) {
        if (!status.canTransitionTo(next)) {
            throw new IllegalTransitionException(id, status, next);
        }
        this.status = next;
        this.updatedAt = now;
    }

    public void markSubmitted(String schemeReference, Instant now) {
        transitionTo(TransferStatus.SUBMITTED, now);
        this.schemeReference = schemeReference;
    }

    public void markFailed(String reason, Instant now) {
        transitionTo(TransferStatus.FAILED, now);
        this.failureReason = reason;
    }

    public String getId() {
        return id;
    }

    public String getPartnerId() {
        return partnerId;
    }

    public String getPartnerReference() {
        return partnerReference;
    }

    public Money getAmount() {
        return new Money(amountMinorUnits, Currency.getInstance(currencyCode));
    }

    public TransferStatus getStatus() {
        return status;
    }

    public String getSchemeReference() {
        return schemeReference;
    }

    public String getFailureReason() {
        return failureReason;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public long getVersion() {
        return version;
    }
}
