package com.paykit.payment.config;

import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import org.springframework.boot.context.properties.ConfigurationProperties;

import java.math.BigDecimal;

/**
 * Pricing and limits. Fees belong in configuration, not in a constant buried in a service:
 * they change, they differ per region, and someone who is not a Java developer needs to be
 * able to see what they currently are.
 */
@ConfigurationProperties(prefix = "paykit.payments")
public record PaymentProperties(
        BigDecimal feePercentage,
        long feeFixedMinor,
        long maxAmountMinor,
        long minAmountMinor) {

    public PaymentProperties {
        if (feePercentage == null) {
            feePercentage = new BigDecimal("0.029");   // 2.9%
        }
        if (feeFixedMinor <= 0) {
            feeFixedMinor = 30;                        // + 30 cents
        }
        if (maxAmountMinor <= 0) {
            maxAmountMinor = 99_999_999L;              // 999,999.99
        }
        if (minAmountMinor <= 0) {
            minAmountMinor = 50;                       // card networks reject dust
        }
    }

    /**
     * The classic "percentage plus fixed" processing fee.
     *
     * <p>The fixed component exists because the cost of a transaction is not proportional to
     * its size — authorising 1 euro costs the network about the same as authorising 1000.
     */
    public Money feeFor(Money amount) {
        return amount.percentage(feePercentage)
                .plus(Money.of(feeFixedMinor, amount.currency()));
    }

    public Money maxAmount(Currency currency) {
        return Money.of(maxAmountMinor, currency);
    }

    public Money minAmount(Currency currency) {
        return Money.of(minAmountMinor, currency);
    }
}
