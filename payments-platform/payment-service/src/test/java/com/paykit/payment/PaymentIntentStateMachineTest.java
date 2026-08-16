package com.paykit.payment;

import com.paykit.common.error.Exceptions;
import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import com.paykit.payment.domain.PaymentIntent;
import com.paykit.payment.domain.PaymentIntentStatus;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * The state machine is the safety rail around every payment. These tests exist to make sure
 * it cannot be walked around.
 */
class PaymentIntentStateMachineTest {

    private static PaymentIntent newIntent() {
        return new PaymentIntent("pi_test", "acct_1", Money.of(10_000L, Currency.EUR),
                "cus_1", "Coffee subscription", Map.of("order_id", "ord_42"));
    }

    @Nested
    @DisplayName("allowed transitions")
    class Allowed {

        @Test
        void newIntentAwaitsConfirmation() {
            assertThat(newIntent().getStatus()).isEqualTo(PaymentIntentStatus.REQUIRES_CONFIRMATION);
        }

        @Test
        void confirmThenSucceed() {
            PaymentIntent intent = newIntent();

            intent.confirm("pm_card_visa");
            assertThat(intent.getStatus()).isEqualTo(PaymentIntentStatus.PROCESSING);
            assertThat(intent.getAttemptCount()).isEqualTo(1);
            assertThat(intent.getConfirmedAt()).isNotNull();

            intent.markSucceeded("ch_1");
            assertThat(intent.getStatus()).isEqualTo(PaymentIntentStatus.SUCCEEDED);
            assertThat(intent.getChargeId()).isEqualTo("ch_1");
            assertThat(intent.getSucceededAt()).isNotNull();
        }

        @Test
        @DisplayName("a declined payment can be retried with another card")
        void failedIntentCanBeRetried() {
            PaymentIntent intent = newIntent();
            intent.confirm("pm_card_declined");
            intent.markFailed("card_declined", "Your card was declined.");

            assertThat(intent.getStatus()).isEqualTo(PaymentIntentStatus.FAILED);
            assertThat(intent.getFailureCode()).isEqualTo("card_declined");

            intent.confirm("pm_card_visa");

            assertThat(intent.getStatus()).isEqualTo(PaymentIntentStatus.PROCESSING);
            assertThat(intent.getAttemptCount()).isEqualTo(2);
            // The previous failure is cleared, so a stale decline code cannot be shown
            // alongside a successful retry.
            assertThat(intent.getFailureCode()).isNull();
        }

        @Test
        void cancelBeforeConfirmation() {
            PaymentIntent intent = newIntent();
            intent.cancel("customer changed their mind");

            assertThat(intent.getStatus()).isEqualTo(PaymentIntentStatus.CANCELED);
            assertThat(intent.getMetadata()).containsEntry("cancellation_reason", "customer changed their mind");
        }
    }

    @Nested
    @DisplayName("forbidden transitions")
    class Forbidden {

        @Test
        @DisplayName("a captured payment cannot be cancelled — that is what a refund is for")
        void cannotCancelASucceededPayment() {
            PaymentIntent intent = newIntent();
            intent.confirm("pm_card_visa");
            intent.markSucceeded("ch_1");

            assertThatThrownBy(() -> intent.cancel("oops"))
                    .isInstanceOf(Exceptions.InvalidStateTransitionException.class)
                    .hasMessageContaining("succeeded")
                    .hasMessageContaining("canceled");
        }

        @Test
        @DisplayName("the double-charge guard: a succeeded payment cannot be confirmed again")
        void cannotConfirmTwice() {
            PaymentIntent intent = newIntent();
            intent.confirm("pm_card_visa");
            intent.markSucceeded("ch_1");

            assertThatThrownBy(() -> intent.confirm("pm_card_visa"))
                    .isInstanceOf(Exceptions.InvalidStateTransitionException.class);
        }

        @Test
        void cannotSucceedWithoutConfirming() {
            assertThatThrownBy(() -> newIntent().markSucceeded("ch_1"))
                    .isInstanceOf(Exceptions.InvalidStateTransitionException.class);
        }

        @Test
        void cannotReviveACanceledPayment() {
            PaymentIntent intent = newIntent();
            intent.cancel(null);

            assertThatThrownBy(() -> intent.confirm("pm_card_visa"))
                    .isInstanceOf(Exceptions.InvalidStateTransitionException.class);
        }

        @Test
        void rejectsANonPositiveAmountAtConstruction() {
            assertThatThrownBy(() -> new PaymentIntent(
                    "pi_x", "acct_1", Money.of(0L, Currency.EUR), null, null, null))
                    .isInstanceOf(Exceptions.InvalidRequestException.class)
                    .hasMessageContaining("greater than zero");
        }
    }

    @Nested
    @DisplayName("the transition table itself")
    class TransitionTable {

        @Test
        void terminalStatesHaveNoSuccessors() {
            assertThat(PaymentIntentStatus.SUCCEEDED.isTerminal()).isTrue();
            assertThat(PaymentIntentStatus.CANCELED.isTerminal()).isTrue();
            assertThat(PaymentIntentStatus.PROCESSING.isTerminal()).isFalse();
            // FAILED is deliberately not terminal — the customer may try another card.
            assertThat(PaymentIntentStatus.FAILED.isTerminal()).isFalse();
        }

        @Test
        void onlySucceededIsRefundable() {
            assertThat(PaymentIntentStatus.SUCCEEDED.isRefundable()).isTrue();
            assertThat(PaymentIntentStatus.PROCESSING.isRefundable()).isFalse();
            assertThat(PaymentIntentStatus.FAILED.isRefundable()).isFalse();
        }

        @ParameterizedTest
        @EnumSource(PaymentIntentStatus.class)
        @DisplayName("no state can transition to itself")
        void noSelfTransitions(PaymentIntentStatus status) {
            assertThat(status.canTransitionTo(status)).isFalse();
        }

        @ParameterizedTest
        @EnumSource(value = PaymentIntentStatus.class, names = {"SUCCEEDED", "CANCELED"})
        void terminalStatesAcceptNothing(PaymentIntentStatus terminal) {
            for (PaymentIntentStatus target : PaymentIntentStatus.values()) {
                assertThat(terminal.canTransitionTo(target))
                        .as("%s -> %s must be rejected", terminal, target)
                        .isFalse();
            }
        }
    }
}
