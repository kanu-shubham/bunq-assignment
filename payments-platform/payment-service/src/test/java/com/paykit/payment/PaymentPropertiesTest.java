package com.paykit.payment;

import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import com.paykit.payment.config.PaymentProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;

class PaymentPropertiesTest {

    private final PaymentProperties properties =
            new PaymentProperties(new BigDecimal("0.029"), 30, 99_999_999L, 50L);

    @ParameterizedTest(name = "{0} cents -> fee {1} cents")
    @CsvSource({
            "10000, 320",   // 2.90 + 0.30
            " 1000,  59",   // 0.29 + 0.30
            "  100,  33",   // 0.03 + 0.30
            "   50,  31"    // the fixed component dominates small payments
    })
    void computesPercentagePlusFixedFee(long amount, long expectedFee) {
        assertThat(properties.feeFor(Money.of(amount, Currency.EUR)))
                .isEqualTo(Money.of(expectedFee, Currency.EUR));
    }

    @Test
    void defaultsApplyWhenConfigurationIsAbsent() {
        PaymentProperties defaults = new PaymentProperties(null, 0, 0, 0);

        assertThat(defaults.feePercentage()).isEqualByComparingTo("0.029");
        assertThat(defaults.feeFixedMinor()).isEqualTo(30L);
        assertThat(defaults.minAmount(Currency.USD)).isEqualTo(Money.of(50L, Currency.USD));
    }

    @Test
    void feeIsAlwaysSmallerThanTheAmountItIsChargedOn() {
        // Sanity: the platform must never take more than 100% of a payment above the floor.
        for (long amount = 50; amount <= 100_000; amount += 137) {
            Money gross = Money.of(amount, Currency.EUR);
            assertThat(properties.feeFor(gross).minorUnits())
                    .as("fee for %d", amount)
                    .isLessThan(amount);
        }
    }
}
