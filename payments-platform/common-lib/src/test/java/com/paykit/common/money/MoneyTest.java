package com.paykit.common.money;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * TESTING CONCEPT — {@code @Nested} groups related cases so the report reads like a
 * specification, and {@code @ParameterizedTest} covers a table of inputs without
 * copy-pasting the test body.
 */
class MoneyTest {

    @Nested
    @DisplayName("construction")
    class Construction {

        @Test
        void rejectsNullCurrency() {
            assertThatThrownBy(() -> new Money(100L, null))
                    .isInstanceOf(NullPointerException.class)
                    .hasMessageContaining("currency");
        }

        @Test
        void convertsMajorUnitsToMinorUnits() {
            assertThat(Money.fromMajor(new BigDecimal("12.34"), Currency.USD).minorUnits()).isEqualTo(1234L);
        }

        @Test
        void respectsZeroDecimalCurrencies() {
            assertThat(Money.fromMajor(new BigDecimal("1250"), Currency.JPY).minorUnits()).isEqualTo(1250L);
        }

        @Test
        void roundsHalfEvenWhenMajorAmountIsTooPrecise() {
            // 0.125 -> 12.5 cents -> banker's rounding picks the even neighbour, 12.
            assertThat(Money.fromMajor(new BigDecimal("0.125"), Currency.USD).minorUnits()).isEqualTo(12L);
        }
    }

    @Nested
    @DisplayName("arithmetic")
    class Arithmetic {

        @Test
        void addsAndSubtracts() {
            Money ten = Money.of(1000L, Currency.EUR);
            Money three = Money.of(300L, Currency.EUR);

            assertThat(ten.plus(three)).isEqualTo(Money.of(1300L, Currency.EUR));
            assertThat(ten.minus(three)).isEqualTo(Money.of(700L, Currency.EUR));
        }

        @Test
        void refusesToMixCurrencies() {
            assertThatThrownBy(() -> Money.of(100L, Currency.USD).plus(Money.of(100L, Currency.EUR)))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("Currency mismatch");
        }

        @Test
        void overflowFailsLoudlyRatherThanWrappingAround() {
            assertThatThrownBy(() -> Money.of(Long.MAX_VALUE, Currency.USD).plus(Money.of(1L, Currency.USD)))
                    .isInstanceOf(ArithmeticException.class);
        }

        @ParameterizedTest(name = "{0} cents at {1} fee = {2} cents")
        @CsvSource({
                "10000, 0.029, 290",
                "  999, 0.029,  29",
                "    1, 0.029,   0",
                "  150, 0.029,   4"
        })
        void computesProcessingFee(long amount, String rate, long expectedFee) {
            assertThat(Money.of(amount, Currency.USD).percentage(new BigDecimal(rate)))
                    .isEqualTo(Money.of(expectedFee, Currency.USD));
        }
    }

    @Nested
    @DisplayName("value semantics")
    class ValueSemantics {

        @Test
        void equalAmountsAreInterchangeable() {
            // The record's generated equals/hashCode is what makes Money a value object.
            assertThat(Money.of(500L, Currency.GBP))
                    .isEqualTo(Money.of(500L, Currency.GBP))
                    .hasSameHashCodeAs(Money.of(500L, Currency.GBP));
        }

        @Test
        void differentCurrenciesAreNotEqual() {
            assertThat(Money.of(500L, Currency.GBP)).isNotEqualTo(Money.of(500L, Currency.USD));
        }

        @Test
        void comparesOrder() {
            assertThat(Money.of(500L, Currency.USD).isGreaterThan(Money.of(100L, Currency.USD))).isTrue();
            assertThat(Money.of(100L, Currency.USD).isLessThan(Money.of(500L, Currency.USD))).isTrue();
        }

        @Test
        void formatsForHumans() {
            assertThat(Money.of(1999L, Currency.USD)).hasToString("$19.99 USD");
            assertThat(Money.of(1250L, Currency.JPY)).hasToString("¥1250 JPY");
        }
    }
}
