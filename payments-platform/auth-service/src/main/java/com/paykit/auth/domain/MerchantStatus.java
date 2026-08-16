package com.paykit.auth.domain;

public enum MerchantStatus {
    /** Signed up, not yet verified — can create test-mode keys only. */
    PENDING,
    ACTIVE,
    /** Temporarily blocked, e.g. for review. Existing payments settle, new ones are refused. */
    SUSPENDED,
    CLOSED
}
