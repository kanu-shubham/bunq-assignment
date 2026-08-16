package com.paykit.payment;

import com.paykit.common.error.Exceptions;
import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import com.paykit.payment.acquirer.AcquirerClient;
import com.paykit.payment.acquirer.AcquirerModels;
import com.paykit.payment.domain.Charge;
import com.paykit.payment.domain.PaymentIntent;
import com.paykit.payment.metrics.PaymentMetrics;
import com.paykit.payment.repository.ChargeRepository;
import com.paykit.payment.repository.PaymentIntentRepository;
import com.paykit.payment.service.PaymentIntentService;
import com.paykit.payment.service.PaymentTransactions;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The confirm flow, with every collaborator mocked.
 *
 * <p>The point of these tests is the <em>orchestration</em>: what happens when the acquirer
 * approves, declines, or does not answer at all. No database, no Kafka, no HTTP — so they run
 * in milliseconds and fail for exactly one reason.
 */
class PaymentIntentServiceTest {

    private PaymentTransactions transactions;
    private AcquirerClient acquirerClient;
    private PaymentIntentRepository paymentIntentRepository;
    private ChargeRepository chargeRepository;
    private PaymentIntentService service;

    private PaymentIntent intent;

    @BeforeEach
    void setUp() {
        transactions = mock(PaymentTransactions.class);
        acquirerClient = mock(AcquirerClient.class);
        paymentIntentRepository = mock(PaymentIntentRepository.class);
        chargeRepository = mock(ChargeRepository.class);

        service = new PaymentIntentService(
                transactions, acquirerClient, paymentIntentRepository, chargeRepository,
                new PaymentMetrics(new SimpleMeterRegistry()));

        intent = new PaymentIntent("pi_1", "acct_1", Money.of(10_000L, Currency.EUR),
                "cus_1", "Order 42", Map.of());
    }

    @Test
    @DisplayName("an approved authorization produces a charge and a settled intent")
    void confirmSucceeds() {
        intent.confirm("pm_card_visa");
        Charge charge = new Charge("ch_1", "pi_1", "acct_1",
                Money.of(10_000L, Currency.EUR), Money.of(320L, Currency.EUR),
                "visa", "4242", "acqref_1", "pm_card_visa");

        when(transactions.beginConfirmation("acct_1", "pi_1", "pm_card_visa")).thenReturn(intent);
        when(acquirerClient.authorize(any())).thenReturn(
                AcquirerModels.AuthorizationResponse.approved("acqref_1", "visa", "4242", "interlink"));
        when(transactions.applyApproval(anyString(), anyString(), any())).thenReturn(charge);
        when(paymentIntentRepository.findByIdAndMerchantId("pi_1", "acct_1"))
                .thenReturn(Optional.of(intent));

        PaymentIntentService.ConfirmationResult result =
                service.confirm("acct_1", "pi_1", "pm_card_visa", "idem_key_1");

        assertThat(result.charge().getId()).isEqualTo("ch_1");
        assertThat(result.charge().getNet()).isEqualTo(Money.of(9_680L, Currency.EUR));
        verify(transactions).applyApproval(anyString(), anyString(), any());
        verify(transactions, never()).applyDecline(anyString(), anyString(), any());
    }

    @Test
    @DisplayName("a decline is a 402, and the payment is recorded as failed — not left hanging")
    void confirmDeclined() {
        intent.confirm("pm_card_declined");

        when(transactions.beginConfirmation(anyString(), anyString(), anyString())).thenReturn(intent);
        when(acquirerClient.authorize(any())).thenReturn(
                AcquirerModels.AuthorizationResponse.declined(
                        "insufficient_funds", "Your card has insufficient funds.", "visa", "0002"));
        when(transactions.applyDecline(anyString(), anyString(), any())).thenReturn(intent);

        assertThatThrownBy(() -> service.confirm("acct_1", "pi_1", "pm_card_declined", "idem_key_2"))
                .isInstanceOf(Exceptions.CardDeclinedException.class)
                .hasMessageContaining("insufficient funds")
                .extracting(ex -> ((Exceptions.CardDeclinedException) ex).declineCode())
                .isEqualTo("insufficient_funds");

        verify(transactions).applyDecline(anyString(), anyString(), any());
        verify(transactions, never()).applyApproval(anyString(), anyString(), any());
    }

    @Test
    @DisplayName("when the acquirer is unreachable the payment stays PROCESSING for reconciliation")
    void confirmWithAcquirerDown() {
        intent.confirm("pm_card_timeout");

        when(transactions.beginConfirmation(anyString(), anyString(), anyString())).thenReturn(intent);
        when(acquirerClient.authorize(any())).thenThrow(
                new Exceptions.AcquirerUnavailableException("card network unreachable", null));

        assertThatThrownBy(() -> service.confirm("acct_1", "pi_1", "pm_card_timeout", "idem_key_3"))
                .isInstanceOf(Exceptions.AcquirerUnavailableException.class);

        // Crucially, neither outcome is applied. Guessing "failed" could refuse a payment the
        // customer was charged for; guessing "succeeded" could ship goods for nothing.
        verify(transactions, never()).applyApproval(anyString(), anyString(), any());
        verify(transactions, never()).applyDecline(anyString(), anyString(), any());
    }

    @Test
    @DisplayName("confirming an already-succeeded payment returns the original charge, no second call")
    void confirmIsIdempotentForSettledPayments() {
        intent.confirm("pm_card_visa");
        intent.markSucceeded("ch_1");
        Charge existing = new Charge("ch_1", "pi_1", "acct_1",
                Money.of(10_000L, Currency.EUR), Money.of(320L, Currency.EUR),
                "visa", "4242", "acqref_1", "pm_card_visa");

        when(transactions.beginConfirmation(anyString(), anyString(), anyString())).thenReturn(intent);
        when(chargeRepository.findByPaymentIntentId("pi_1")).thenReturn(Optional.of(existing));

        PaymentIntentService.ConfirmationResult result =
                service.confirm("acct_1", "pi_1", "pm_card_visa", "idem_key_4");

        assertThat(result.charge().getId()).isEqualTo("ch_1");
        // The card is not touched a second time. This is the last line of defence behind
        // the idempotency layer.
        verify(acquirerClient, never()).authorize(any());
    }

    @Test
    @DisplayName("the idempotency key is forwarded to the acquirer so their retry is safe too")
    void forwardsIdempotencyKeyDownstream() {
        intent.confirm("pm_card_visa");
        when(transactions.beginConfirmation(anyString(), anyString(), anyString())).thenReturn(intent);
        when(acquirerClient.authorize(any())).thenReturn(
                AcquirerModels.AuthorizationResponse.approved("acqref_1", "visa", "4242", "interlink"));
        when(transactions.applyApproval(anyString(), anyString(), any())).thenReturn(
                new Charge("ch_1", "pi_1", "acct_1", Money.of(10_000L, Currency.EUR),
                        Money.of(320L, Currency.EUR), "visa", "4242", "acqref_1", "pm_card_visa"));
        when(paymentIntentRepository.findByIdAndMerchantId(anyString(), anyString()))
                .thenReturn(Optional.of(intent));

        service.confirm("acct_1", "pi_1", "pm_card_visa", "idem_key_5");

        ArgumentCaptor<AcquirerModels.AuthorizationRequest> captor =
                ArgumentCaptor.forClass(AcquirerModels.AuthorizationRequest.class);
        verify(acquirerClient).authorize(captor.capture());

        assertThat(captor.getValue().idempotencyKey()).isEqualTo("idem_key_5");
        assertThat(captor.getValue().amountMinor()).isEqualTo(10_000L);
        assertThat(captor.getValue().currency()).isEqualTo(Currency.EUR);
    }
}
