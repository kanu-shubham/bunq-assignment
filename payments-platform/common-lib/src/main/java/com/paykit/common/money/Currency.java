package com.paykit.common.money;

/**
 * Supported settlement currencies.
 *
 * <p>JAVA CONCEPT — enums are full classes. They can carry state ({@code exponent}),
 * expose behaviour ({@link #minorUnitsPerMajor()}) and are the safest way to model a
 * closed set of values: the compiler rejects typos that a {@code String} would happily accept.
 */
public enum Currency {

    USD(2, "$"),
    EUR(2, "€"),
    GBP(2, "£"),
    /** The yen has no sub-unit, which is exactly why money must never be a {@code double}. */
    JPY(0, "¥");

    private final int exponent;
    private final String symbol;

    Currency(int exponent, String symbol) {
        this.exponent = exponent;
        this.symbol = symbol;
    }

    /** Number of decimal digits in the minor unit: 2 for cents, 0 for yen. */
    public int exponent() {
        return exponent;
    }

    public String symbol() {
        return symbol;
    }

    /** 100 for USD/EUR/GBP, 1 for JPY. */
    public long minorUnitsPerMajor() {
        long factor = 1L;
        for (int i = 0; i < exponent; i++) {
            factor *= 10L;
        }
        return factor;
    }
}
