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
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

import java.time.Instant;

/**
 * The immutable-ish record of money actually moved: one successful capture.
 *
 * <p>A {@link PaymentIntent} may be attempted several times (declined, retried with another
 * card); a {@code Charge} exists only for the attempt that worked. Keeping them apart is what
 * lets the intent stay a mutable workflow object while the charge stays an accounting fact.
 *
 * <p>The only field that changes after creation is {@code refundedMinor}, and it is guarded so
 * the total refunded can never exceed what was captured.
 */
@Entity
@Table(name = "charges", indexes = {
        @Index(name = "idx_charge_merchant_created", columnList = "merchant_id, created_at DESC"),
        @Index(name = "idx_charge_payment_intent", columnList = "payment_intent_id")
})
@EntityListeners(AuditingEntityListener.class)
public class Charge {

    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "payment_intent_id", nullable = false, length = 64)
    private String paymentIntentId;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    @Column(name = "amount_minor", nullable = false)
    private long amountMinor;

    /** The platform's cut. Charged on capture, refunded proportionally on refund. */
    @Column(name = "fee_minor", nullable = false)
    private long feeMinor;

    @Column(name = "refunded_minor", nullable = false)
    private long refundedMinor;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 3)
    private Currency currency;

    @Column(name = "card_brand", length = 32)
    private String cardBrand;

    /** Only the last four digits are ever stored. Storing a full PAN pulls you into PCI-DSS scope. */
    @Column(name = "card_last4", length = 4)
    private String cardLast4;

    @Column(name = "acquirer_reference", length = 128)
    private String acquirerReference;

    @Column(name = "payment_method_id", length = 64)
    private String paymentMethodId;

    @Version
    private Long version;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    protected Charge() {
    }

    public Charge(String id, String paymentIntentId, String merchantId, Money amount, Money fee,
                  String cardBrand, String cardLast4, String acquirerReference, String paymentMethodId) {
        this.id = id;
        this.paymentIntentId = paymentIntentId;
        this.merchantId = merchantId;
        this.amountMinor = amount.minorUnits();
        this.currency = amount.currency();
        this.feeMinor = fee.minorUnits();
        this.refundedMinor = 0L;
        this.cardBrand = cardBrand;
        this.cardLast4 = cardLast4;
        this.acquirerReference = acquirerReference;
        this.paymentMethodId = paymentMethodId;
    }

    /**
     * Records a (possibly partial) refund.
     *
     * <p>The over-refund check lives here rather than in the service because this object owns
     * the invariant. A service can forget to call a check; an entity that refuses to enter an
     * invalid state cannot.
     */
    public void recordRefund(Money refundAmount) {
        if (!refundAmount.isPositive()) {
            throw new Exceptions.InvalidRequestException("Refund amount must be greater than zero", "amount");
        }
        if (refundAmount.currency() != currency) {
            throw new Exceptions.InvalidRequestException(
                    "Refund currency %s does not match charge currency %s"
                            .formatted(refundAmount.currency(), currency), "currency");
        }
        Money newTotal = getRefunded().plus(refundAmount);
        if (newTotal.isGreaterThan(getAmount())) {
            throw new Exceptions.InvalidRequestException(
                    "Refund of %s would exceed the remaining refundable amount of %s"
                            .formatted(refundAmount, getRefundable()), "amount");
        }
        this.refundedMinor = newTotal.minorUnits();
    }

    public Money getAmount() {
        return Money.of(amountMinor, currency);
    }

    public Money getFee() {
        return Money.of(feeMinor, currency);
    }

    public Money getRefunded() {
        return Money.of(refundedMinor, currency);
    }

    /** What the merchant nets: captured minus the platform fee. */
    public Money getNet() {
        return getAmount().minus(getFee());
    }

    public Money getRefundable() {
        return getAmount().minus(getRefunded());
    }

    public boolean isFullyRefunded() {
        return refundedMinor >= amountMinor;
    }

    /** Proportional fee refund, so a half refund returns half the fee. */
    public Money feeShareFor(Money refundAmount) {
        if (amountMinor == 0) {
            return Money.zero(currency);
        }
        return Money.of(Math.round((double) feeMinor * refundAmount.minorUnits() / amountMinor), currency);
    }

    public boolean belongsTo(String candidateMerchantId) {
        return merchantId.equals(candidateMerchantId);
    }

    public String getId() {
        return id;
    }

    public String getPaymentIntentId() {
        return paymentIntentId;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public Currency getCurrency() {
        return currency;
    }

    public String getCardBrand() {
        return cardBrand;
    }

    public String getCardLast4() {
        return cardLast4;
    }

    public String getAcquirerReference() {
        return acquirerReference;
    }

    public String getPaymentMethodId() {
        return paymentMethodId;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Long getVersion() {
        return version;
    }
}
