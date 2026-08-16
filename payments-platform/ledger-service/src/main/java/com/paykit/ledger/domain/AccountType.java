package com.paykit.ledger.domain;

/**
 * The chart of accounts, and the direction that increases each one.
 *
 * <h3>Double-entry in one paragraph</h3>
 * Every transaction touches at least two accounts, and the debits must equal the credits.
 * Money is never created or destroyed, only moved. If the two sides do not match, the entry is
 * rejected — which is how a 500-year-old technique catches bugs that a single {@code balance}
 * column silently absorbs.
 *
 * <h3>Which direction is "more"?</h3>
 * The confusing part, and the reason it is encoded here rather than remembered:
 * <ul>
 *   <li><b>Assets</b> (money we hold) increase on the <b>debit</b> side.</li>
 *   <li><b>Liabilities</b> (money we owe merchants) increase on the <b>credit</b> side.</li>
 *   <li><b>Revenue</b> (our fees) increases on the <b>credit</b> side.</li>
 * </ul>
 * A merchant's balance is a <em>liability</em>: we are holding their money for them.
 */
public enum AccountType {

    /** ASSET — funds in flight from the card network to us. */
    CARD_NETWORK_CLEARING(Direction.DEBIT),

    /** LIABILITY — what we owe this merchant. Their "balance". */
    MERCHANT_PAYABLE(Direction.CREDIT),

    /** REVENUE — processing fees we have earned. */
    PLATFORM_FEE_REVENUE(Direction.CREDIT),

    /** ASSET — our own bank account, debited when we actually pay a merchant out. */
    PLATFORM_CASH(Direction.DEBIT);

    private final Direction increasesOn;

    AccountType(Direction increasesOn) {
        this.increasesOn = increasesOn;
    }

    public Direction increasesOn() {
        return increasesOn;
    }

    /** Signed effect of a posting on this account's balance: +1 or -1. */
    public int signFor(Direction direction) {
        return direction == increasesOn ? 1 : -1;
    }

    /** True for accounts that exist per merchant rather than once for the platform. */
    public boolean isPerMerchant() {
        return this == MERCHANT_PAYABLE;
    }
}
