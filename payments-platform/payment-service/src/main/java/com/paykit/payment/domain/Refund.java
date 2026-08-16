package com.paykit.payment.domain;

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
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

import java.time.Instant;

@Entity
@Table(name = "refunds", indexes = {
        @Index(name = "idx_refund_charge", columnList = "charge_id"),
        @Index(name = "idx_refund_merchant_created", columnList = "merchant_id, created_at DESC")
})
@EntityListeners(AuditingEntityListener.class)
public class Refund {

    /** Why the money is going back. Real processors report on this. */
    public enum Reason {
        REQUESTED_BY_CUSTOMER,
        DUPLICATE,
        FRAUDULENT,
        PRODUCT_UNSATISFACTORY
    }

    public enum Status {
        PENDING,
        SUCCEEDED,
        FAILED
    }

    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "charge_id", nullable = false, length = 64)
    private String chargeId;

    @Column(name = "payment_intent_id", nullable = false, length = 64)
    private String paymentIntentId;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    @Column(name = "amount_minor", nullable = false)
    private long amountMinor;

    @Column(name = "fee_refunded_minor", nullable = false)
    private long feeRefundedMinor;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 3)
    private Currency currency;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private Reason reason;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private Status status;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    protected Refund() {
    }

    public Refund(String id, Charge charge, Money amount, Money feeRefunded, Reason reason) {
        this.id = id;
        this.chargeId = charge.getId();
        this.paymentIntentId = charge.getPaymentIntentId();
        this.merchantId = charge.getMerchantId();
        this.amountMinor = amount.minorUnits();
        this.feeRefundedMinor = feeRefunded.minorUnits();
        this.currency = amount.currency();
        this.reason = reason == null ? Reason.REQUESTED_BY_CUSTOMER : reason;
        // Card-network refunds settle in days. This demo completes them immediately;
        // a real implementation would leave them PENDING and update on a network callback.
        this.status = Status.SUCCEEDED;
    }

    public Money getAmount() {
        return Money.of(amountMinor, currency);
    }

    public Money getFeeRefunded() {
        return Money.of(feeRefundedMinor, currency);
    }

    public boolean belongsTo(String candidateMerchantId) {
        return merchantId.equals(candidateMerchantId);
    }

    public String getId() {
        return id;
    }

    public String getChargeId() {
        return chargeId;
    }

    public String getPaymentIntentId() {
        return paymentIntentId;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public Reason getReason() {
        return reason;
    }

    public Status getStatus() {
        return status;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
