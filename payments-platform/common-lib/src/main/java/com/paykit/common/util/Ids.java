package com.paykit.common.util;

import java.security.SecureRandom;

/**
 * Prefixed, URL-safe public identifiers — {@code pi_3Nk9x2LkdIwHu7ix}, {@code ch_1Op...}.
 *
 * <p>WHY — a prefixed id is self-describing in logs and support tickets, and it makes
 * "you passed a charge id where a refund id was expected" obvious at a glance. Random ids
 * also avoid leaking business volume the way an auto-increment primary key does.
 *
 * <p>JAVA CONCEPT — {@code SecureRandom} is thread-safe and seeded from the OS entropy pool.
 * A {@code static final} instance is shared safely across every request thread.
 */
public final class Ids {

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final char[] ALPHABET =
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".toCharArray();
    private static final int DEFAULT_LENGTH = 20;

    public static final String PAYMENT_INTENT = "pi";
    public static final String CHARGE = "ch";
    public static final String REFUND = "re";
    public static final String CUSTOMER = "cus";
    public static final String PAYMENT_METHOD = "pm";
    public static final String EVENT = "evt";
    public static final String MERCHANT = "acct";
    public static final String WEBHOOK_ENDPOINT = "we";
    public static final String API_KEY = "sk";

    /** Utility class: a private constructor stops anyone instantiating it by mistake. */
    private Ids() {
        throw new AssertionError("No instances");
    }

    public static String generate(String prefix) {
        StringBuilder sb = new StringBuilder(prefix.length() + 1 + DEFAULT_LENGTH);
        sb.append(prefix).append('_');
        for (int i = 0; i < DEFAULT_LENGTH; i++) {
            sb.append(ALPHABET[RANDOM.nextInt(ALPHABET.length)]);
        }
        return sb.toString();
    }

    public static boolean hasPrefix(String id, String prefix) {
        return id != null && id.startsWith(prefix + "_");
    }
}
