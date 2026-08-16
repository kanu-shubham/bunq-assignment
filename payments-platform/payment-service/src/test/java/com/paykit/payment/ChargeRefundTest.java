package com.paykit.payment;

import com.paykit.common.error.Exceptions;
import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import com.paykit.payment.domain.Charge;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The refund invariant: you cannot send back more than you took.
 */
class ChargeRefundTest {

    private Charge charge;

    @BeforeEach
    void setUp() {
        // 100.00 EUR captured, 3.20 EUR fee (2.9% + 0.30).
        charge = new Charge("ch_1", "pi_1", "acct_1",
                Money.of(10_000L, Currency.EUR), Money.of(320L, Currency.EUR),
                "visa", "4242", "acqref_1", "pm_1");
    }

    @Test
    void netIsGrossMinusFee() {
        assertThat(charge.getNet()).isEqualTo(Money.of(9_680L, Currency.EUR));
        assertThat(charge.getRefundable()).isEqualTo(Money.of(10_000L, Currency.EUR));
        assertThat(charge.isFullyRefunded()).isFalse();
    }

    @Test
    void partialRefundsAccumulate() {
        charge.recordRefund(Money.of(3_000L, Currency.EUR));
        assertThat(charge.getRefunded()).isEqualTo(Money.of(3_000L, Currency.EUR));
        assertThat(charge.getRefundable()).isEqualTo(Money.of(7_000L, Currency.EUR));

        charge.recordRefund(Money.of(7_000L, Currency.EUR));
        assertThat(charge.isFullyRefunded()).isTrue();
        assertThat(charge.getRefundable()).isEqualTo(Money.zero(Currency.EUR));
    }

    @Test
    @DisplayName("the invariant: refunding more than was captured is rejected")
    void cannotOverRefundInOneGo() {
        assertThatThrownBy(() -> charge.recordRefund(Money.of(10_001L, Currency.EUR)))
                .isInstanceOf(Exceptions.InvalidRequestException.class)
                .hasMessageContaining("exceed");

        assertThat(charge.getRefunded()).isEqualTo(Money.zero(Currency.EUR));
    }

    @Test
    @DisplayName("...or across several partial refunds")
    void cannotOverRefundIncrementally() {
        charge.recordRefund(Money.of(6_000L, Currency.EUR));

        assertThatThrownBy(() -> charge.recordRefund(Money.of(4_001L, Currency.EUR)))
                .isInstanceOf(Exceptions.InvalidRequestException.class);

        // The rejected refund left no trace.
        assertThat(charge.getRefunded()).isEqualTo(Money.of(6_000L, Currency.EUR));
    }

    @Test
    void rejectsZeroAndNegativeRefunds() {
        assertThatThrownBy(() -> charge.recordRefund(Money.zero(Currency.EUR)))
                .isInstanceOf(Exceptions.InvalidRequestException.class);
        assertThatThrownBy(() -> charge.recordRefund(Money.of(-100L, Currency.EUR)))
                .isInstanceOf(Exceptions.InvalidRequestException.class);
    }

    @Test
    void rejectsACurrencyMismatch() {
        assertThatThrownBy(() -> charge.recordRefund(Money.of(1_000L, Currency.USD)))
                .isInstanceOf(Exceptions.InvalidRequestException.class)
                .hasMessageContaining("does not match");
    }

    @Test
    @DisplayName("the fee comes back in proportion to the amount refunded")
    void feeIsRefundedProportionally() {
        assertThat(charge.feeShareFor(Money.of(10_000L, Currency.EUR)))
                .isEqualTo(Money.of(320L, Currency.EUR));
        assertThat(charge.feeShareFor(Money.of(5_000L, Currency.EUR)))
                .isEqualTo(Money.of(160L, Currency.EUR));
        assertThat(charge.feeShareFor(Money.of(2_500L, Currency.EUR)))
                .isEqualTo(Money.of(80L, Currency.EUR));
    }

    @Test
    void tenantIsolationIsCheckedOnTheEntity() {
        assertThat(charge.belongsTo("acct_1")).isTrue();
        assertThat(charge.belongsTo("acct_someone_else")).isFalse();
    }
}
