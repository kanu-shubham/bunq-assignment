package com.paykit.ledger.service;

import com.paykit.common.money.Money;
import com.paykit.ledger.domain.AccountType;
import com.paykit.ledger.domain.Direction;

/** One side of a transaction, before it is turned into a persisted entry. */
public record LedgerPosting(AccountType accountType, Direction direction, Money amount, String merchantId) {

    public static LedgerPosting debit(AccountType type, Money amount, String merchantId) {
        return new LedgerPosting(type, Direction.DEBIT, amount, merchantId);
    }

    public static LedgerPosting credit(AccountType type, Money amount, String merchantId) {
        return new LedgerPosting(type, Direction.CREDIT, amount, merchantId);
    }
}
