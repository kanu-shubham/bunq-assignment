package com.example.prep.partnersend.domain;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class MoneyTest {

    @Test
    @DisplayName("the reason money is never a double")
    void floatingPointCannotRepresentMoney() {
        // This is not a Java quirk — it is IEEE-754, and it is true in JavaScript,
        // Python and everywhere else. 0.1 has no exact binary representation.
        double wrong = 0.1 + 0.2;
        assertThat(wrong).isNotEqualTo(0.3);
        assertThat(wrong).isEqualTo(0.30000000000000004);

        // Integers of minor units have no such problem.
        Money tenP = Money.of("GBP", 10);
        Money twentyP = Money.of("GBP", 20);
        assertThat(tenP.plus(twentyP)).isEqualTo(Money.of("GBP", 30));
    }

    @Test
    @DisplayName("BigDecimal only helps if constructed from a string")
    void bigDecimalFromDoubleIsStillWrong() {
        // A trap worth recognising on sight: new BigDecimal(double) faithfully copies the
        // double's error, so it looks precise and is not.
        assertThat(new BigDecimal(0.1).toPlainString()).startsWith("0.1000000000000000055511151231257827");
        assertThat(new BigDecimal("0.1").toPlainString()).isEqualTo("0.1");
    }

    @Test
    void addingDifferentCurrenciesIsARejectedBug() {
        assertThatThrownBy(() -> Money.of("GBP", 100).plus(Money.of("EUR", 100)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Currency mismatch");
    }

    @Test
    @DisplayName("minor-unit scale is per currency, not always 2")
    void currencyScaleVaries() {
        assertThat(Money.parse("GBP", "12.34").minorUnits()).isEqualTo(1234);
        assertThat(Money.parse("JPY", "1234").minorUnits()).isEqualTo(1234);   // no decimals
        assertThat(Money.parse("KWD", "12.345").minorUnits()).isEqualTo(12345); // three
    }

    @Test
    @DisplayName("excess precision is a malformed request, not a rounding opportunity")
    void rejectsMorePrecisionThanTheCurrencyHas() {
        assertThatThrownBy(() -> Money.parse("GBP", "12.345"))
                .isInstanceOf(ArithmeticException.class);
    }

    @Test
    void overflowIsLoudRatherThanSilent() {
        // Math.addExact throws instead of wrapping round to a negative balance.
        assertThatThrownBy(() -> Money.of("GBP", Long.MAX_VALUE).plus(Money.of("GBP", 1)))
                .isInstanceOf(ArithmeticException.class);
    }

    @Test
    void roundTripsThroughDecimal() {
        assertThat(Money.of("GBP", 1234).toDecimal()).isEqualByComparingTo("12.34");
        assertThat(Money.of("GBP", 1234)).hasToString("12.34 GBP");
    }
}
