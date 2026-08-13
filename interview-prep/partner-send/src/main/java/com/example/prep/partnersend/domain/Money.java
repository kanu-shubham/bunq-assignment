package com.example.prep.partnersend.domain;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Currency;
import java.util.Objects;

/**
 * An exact monetary amount, stored as an integer number of <em>minor units</em>
 * (pence, cents, satoshi-of-the-fiat-world) plus its currency.
 *
 * <p>Three rules this type exists to enforce, all of which come up in payment
 * interviews:
 *
 * <ol>
 *   <li><b>Never {@code double}.</b> Binary floating point cannot represent 0.1
 *       exactly, so {@code 0.1 + 0.2 != 0.3}. See {@code MoneyTest}. Money is
 *       counted, not measured — use integers.</li>
 *   <li><b>Currency is part of the value.</b> Adding GBP to EUR is a bug, not a
 *       conversion. The compiler cannot catch it, so the type does, at runtime.</li>
 *   <li><b>Scale is per-currency.</b> GBP has 2 decimal places, JPY has 0, and
 *       several dinar currencies have 3. Hard-coding "multiply by 100" breaks in
 *       Tokyo and Kuwait.</li>
 * </ol>
 *
 * <p>A {@code record} is Java's version of an immutable data class: the compiler
 * generates the constructor, accessors, {@code equals}, {@code hashCode} and
 * {@code toString}. The accessors are named {@code minorUnits()} — no {@code get}
 * prefix.
 */
public record Money(long minorUnits, Currency currency) implements Comparable<Money> {

    /**
     * The "compact constructor" — runs before the fields are assigned, so it is
     * where validation lives.
     */
    public Money {
        Objects.requireNonNull(currency, "currency");
    }

    public static Money of(String currencyCode, long minorUnits) {
        return new Money(minorUnits, Currency.getInstance(currencyCode));
    }

    public static Money zero(String currencyCode) {
        return of(currencyCode, 0L);
    }

    /**
     * Parses a human-facing decimal string ("12.34") into minor units, rejecting
     * anything with more precision than the currency actually has. "12.345" GBP
     * is not a rounding opportunity — it is a malformed request, and silently
     * rounding it is how you end up with a penny of unexplained drift per
     * thousand payments.
     */
    public static Money parse(String currencyCode, String decimalAmount) {
        Currency currency = Currency.getInstance(currencyCode);
        int scale = currency.getDefaultFractionDigits();
        BigDecimal value = new BigDecimal(decimalAmount);
        // setScale with UNNECESSARY throws ArithmeticException rather than rounding.
        BigDecimal scaled = value.setScale(scale, RoundingMode.UNNECESSARY);
        return new Money(scaled.movePointRight(scale).longValueExact(), currency);
    }

    public BigDecimal toDecimal() {
        return BigDecimal.valueOf(minorUnits, currency.getDefaultFractionDigits());
    }

    public Money plus(Money other) {
        requireSameCurrency(other);
        return new Money(Math.addExact(minorUnits, other.minorUnits), currency);
    }

    public Money minus(Money other) {
        requireSameCurrency(other);
        return new Money(Math.subtractExact(minorUnits, other.minorUnits), currency);
    }

    public Money negated() {
        return new Money(Math.negateExact(minorUnits), currency);
    }

    public boolean isPositive() {
        return minorUnits > 0;
    }

    public boolean isZero() {
        return minorUnits == 0;
    }

    private void requireSameCurrency(Money other) {
        if (!currency.equals(other.currency)) {
            throw new IllegalArgumentException(
                    "Currency mismatch: %s vs %s".formatted(currency, other.currency));
        }
    }

    @Override
    public int compareTo(Money other) {
        requireSameCurrency(other);
        return Long.compare(minorUnits, other.minorUnits);
    }

    @Override
    public String toString() {
        return "%s %s".formatted(toDecimal().toPlainString(), currency.getCurrencyCode());
    }
}
