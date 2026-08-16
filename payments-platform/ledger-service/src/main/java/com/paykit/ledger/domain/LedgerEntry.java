package com.paykit.ledger.domain;

import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;

import java.time.Instant;

/**
 * A single posting: this much money, this direction, this account.
 *
 * <p>Ledger entries are <b>append-only</b>. There is no update method and no delete: a mistake
 * is corrected by posting a reversing entry, never by editing history. That is what makes the
 * ledger auditable — you can always answer "what did we believe, and when did we believe it?"
 */
@Entity
@Table(name = "ledger_entries", indexes = {
        @Index(name = "idx_entry_account", columnList = "account_id, created_at DESC"),
        @Index(name = "idx_entry_transaction", columnList = "transaction_id"),
        @Index(name = "idx_entry_merchant", columnList = "merchant_id, created_at DESC")
})
public class LedgerEntry {

    @Id
    @Column(length = 64)
    private String id;

    /** Groups the two-or-more sides of one balanced transaction. */
    @Column(name = "transaction_id", nullable = false, length = 64)
    private String transactionId;

    @Column(name = "account_id", nullable = false, length = 96)
    private String accountId;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 6)
    private Direction direction;

    @Column(name = "amount_minor", nullable = false)
    private long amountMinor;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 3)
    private Currency currency;

    /** The Kafka event that produced this posting — the audit trail back to the payment. */
    @Column(name = "source_event_id", nullable = false, length = 64)
    private String sourceEventId;

    @Column(name = "source_type", nullable = false, length = 64)
    private String sourceType;

    @Column(name = "reference_id", length = 64)
    private String referenceId;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected LedgerEntry() {
    }

    public LedgerEntry(String id, String transactionId, String accountId, String merchantId,
                       Direction direction, Money amount, String sourceEventId,
                       String sourceType, String referenceId) {
        this.id = id;
        this.transactionId = transactionId;
        this.accountId = accountId;
        this.merchantId = merchantId;
        this.direction = direction;
        this.amountMinor = amount.minorUnits();
        this.currency = amount.currency();
        this.sourceEventId = sourceEventId;
        this.sourceType = sourceType;
        this.referenceId = referenceId;
        this.createdAt = Instant.now();
    }

    public Money getAmount() {
        return Money.of(amountMinor, currency);
    }

    public String getId() {
        return id;
    }

    public String getTransactionId() {
        return transactionId;
    }

    public String getAccountId() {
        return accountId;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public Direction getDirection() {
        return direction;
    }

    public Currency getCurrency() {
        return currency;
    }

    public String getSourceEventId() {
        return sourceEventId;
    }

    public String getSourceType() {
        return sourceType;
    }

    public String getReferenceId() {
        return referenceId;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
