package com.paykit.ledger.domain;

import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import jakarta.persistence.Version;

import java.time.Instant;

/**
 * One account in the chart of accounts, scoped to a merchant and a currency.
 *
 * <p>The running {@code balanceMinor} is a <b>cache</b>. The authoritative balance is the sum
 * of the postings in {@link LedgerEntry}, and {@code BalanceService} can recompute it to prove
 * the cache is right. Storing only the running total would make a bug unrecoverable; storing
 * only the postings would make every balance read an aggregate over millions of rows. Keeping
 * both, and being able to check one against the other, is the point.
 */
@Entity
@Table(name = "ledger_accounts", uniqueConstraints = @UniqueConstraint(
        name = "uq_ledger_account", columnNames = {"merchant_id", "account_type", "currency"}))
public class LedgerAccount {

    @Id
    @Column(length = 96)
    private String id;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    @Enumerated(EnumType.STRING)
    @Column(name = "account_type", nullable = false, length = 40)
    private AccountType accountType;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 3)
    private Currency currency;

    @Column(name = "balance_minor", nullable = false)
    private long balanceMinor;

    @Version
    private Long version;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected LedgerAccount() {
    }

    public LedgerAccount(String merchantId, AccountType accountType, Currency currency) {
        this.id = idFor(merchantId, accountType, currency);
        this.merchantId = merchantId;
        this.accountType = accountType;
        this.currency = currency;
        this.balanceMinor = 0L;
        this.createdAt = Instant.now();
    }

    /**
     * A deterministic id, so "get or create this account" needs no sequence and no extra
     * lookup — and two concurrent creations collide on the primary key instead of producing
     * two accounts for the same thing.
     */
    public static String idFor(String merchantId, AccountType accountType, Currency currency) {
        String scope = accountType.isPerMerchant() ? merchantId : "platform";
        return "acc_%s_%s_%s".formatted(scope, accountType.name().toLowerCase(), currency.name());
    }

    public void apply(Direction direction, Money amount) {
        if (amount.currency() != currency) {
            throw new IllegalArgumentException(
                    "Cannot post %s to a %s account".formatted(amount.currency(), currency));
        }
        this.balanceMinor += (long) accountType.signFor(direction) * amount.minorUnits();
    }

    public Money getBalance() {
        return Money.of(balanceMinor, currency);
    }

    public String getId() {
        return id;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public AccountType getAccountType() {
        return accountType;
    }

    public Currency getCurrency() {
        return currency;
    }

    public Long getVersion() {
        return version;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
