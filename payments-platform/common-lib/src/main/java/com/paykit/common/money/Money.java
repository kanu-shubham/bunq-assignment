package com.paykit.common.money;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.math.BigDecimal;
import java.math.MathContext;
import java.math.RoundingMode;
import java.util.Objects;

/**
 * An immutable amount of money, stored in <em>minor units</em> (cents, pence, yen).
 *
 * <p>JAVA CONCEPT — {@code record}. A record is a transparent carrier for immutable data:
 * the compiler generates the constructor, accessors, {@code equals}, {@code hashCode} and
 * {@code toString}. That makes it the natural shape for a value object, where two instances
 * with the same fields <em>are</em> the same thing.
 *
 * <p>DOMAIN RULE — never use {@code double} for money. {@code 0.1 + 0.2 != 0.3} in binary
 * floating point, and a payments ledger that drifts by a fraction of a cent per transaction
 * will not balance. Every real payment processor stores integer minor units, and so do we.
 */
public record Money(
        @JsonProperty("amount") long minorUnits,
        @JsonProperty("currency") Currency currency) implements Comparable<Money> {

    /**
     * The <em>compact constructor</em> runs before the fields are assigned, which makes it
     * the right place for invariants. An object that fails validation is never constructed,
     * so no other code has to defend against a {@code Money} with a null currency.
     */
    public Money {
        Objects.requireNonNull(currency, "currency must not be null");
    }

    @JsonCreator
    public static Money of(@JsonProperty("amount") long minorUnits,
                           @JsonProperty("currency") Currency currency) {
        return new Money(minorUnits, currency);
    }

    public static Money zero(Currency currency) {
        return new Money(0L, currency);
    }

    /** Builds from a major-unit amount, e.g. {@code fromMajor(new BigDecimal("12.34"), USD)} -> 1234 cents. */
    public static Money fromMajor(BigDecimal major, Currency currency) {
        Objects.requireNonNull(major, "major must not be null");
        return new Money(
                major.movePointRight(currency.exponent()).setScale(0, RoundingMode.HALF_EVEN).longValueExact(),
                currency);
    }

    /**
     * Readable alias for {@link #minorUnits()}. Not a JSON property — the component
     * above already publishes this value under the name {@code amount}.
     */
    public long amount() {
        return minorUnits;
    }

    public Money plus(Money other) {
        requireSameCurrency(other);
        return new Money(Math.addExact(minorUnits, other.minorUnits), currency);
    }

    public Money minus(Money other) {
        requireSameCurrency(other);
        return new Money(Math.subtractExact(minorUnits, other.minorUnits), currency);
    }

    /**
     * Applies a rate such as a 2.9% processing fee, rounding half-even ("banker's rounding")
     * so that repeated rounding does not systematically favour one party.
     */
    public Money percentage(BigDecimal rate) {
        BigDecimal result = BigDecimal.valueOf(minorUnits)
                .multiply(rate, MathContext.DECIMAL64)
                .setScale(0, RoundingMode.HALF_EVEN);
        return new Money(result.longValueExact(), currency);
    }

    public Money negated() {
        return new Money(Math.negateExact(minorUnits), currency);
    }

    /*
     * JACKSON GOTCHA — isXxx() looks like a boolean bean getter, so Jackson would publish
     * "zero", "positive" and "negative" as JSON fields and then refuse to read its own
     * output back. @JsonIgnore keeps the wire format to exactly {amount, currency}.
     */
    @JsonIgnore
    public boolean isPositive() {
        return minorUnits > 0;
    }

    @JsonIgnore
    public boolean isZero() {
        return minorUnits == 0;
    }

    @JsonIgnore
    public boolean isNegative() {
        return minorUnits < 0;
    }

    public boolean isGreaterThan(Money other) {
        return compareTo(other) > 0;
    }

    public boolean isLessThan(Money other) {
        return compareTo(other) < 0;
    }

    public BigDecimal toMajor() {
        return BigDecimal.valueOf(minorUnits).movePointLeft(currency.exponent());
    }

    @Override
    public int compareTo(Money other) {
        requireSameCurrency(other);
        return Long.compare(minorUnits, other.minorUnits);
    }

    private void requireSameCurrency(Money other) {
        Objects.requireNonNull(other, "other must not be null");
        if (currency != other.currency) {
            // JAVA CONCEPT — unchecked exception for a programming error the caller
            // cannot sensibly recover from at runtime.
            throw new IllegalArgumentException(
                    "Currency mismatch: cannot combine %s with %s".formatted(currency, other.currency));
        }
    }

    @Override
    public String toString() {
        return "%s%s %s".formatted(currency.symbol(), toMajor().toPlainString(), currency);
    }
}
