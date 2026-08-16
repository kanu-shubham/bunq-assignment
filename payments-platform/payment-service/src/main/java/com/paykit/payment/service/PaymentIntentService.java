package com.paykit.payment.service;

import com.paykit.common.error.Exceptions;
import com.paykit.common.money.Money;
import com.paykit.payment.acquirer.AcquirerClient;
import com.paykit.payment.acquirer.AcquirerModels;
import com.paykit.payment.domain.Charge;
import com.paykit.payment.domain.PaymentIntent;
import com.paykit.payment.domain.PaymentIntentStatus;
import com.paykit.payment.metrics.PaymentMetrics;
import com.paykit.payment.repository.ChargeRepository;
import com.paykit.payment.repository.PaymentIntentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Map;
import java.util.Optional;

/**
 * Orchestrates the payment lifecycle.
 *
 * <p>Note what this class does <em>not</em> have: {@code @Transactional} on {@link #confirm}.
 * That is the entire design. The transactions live in {@link PaymentTransactions} and are
 * deliberately short, with the slow, failure-prone network call happening between them rather
 * than inside them.
 *
 * <h3>The confirm flow, and where it can break</h3>
 * <pre>
 *   1. beginConfirmation   [tx]   REQUIRES_CONFIRMATION -> PROCESSING, committed
 *   2. acquirer.authorize  [ ]    the slow bit; retried, timed out, circuit broken
 *   3. applyApproval /
 *      applyDecline        [tx]   PROCESSING -> SUCCEEDED | FAILED, event recorded
 * </pre>
 *
 * <p>If the process dies between 2 and 3, the payment is stranded in PROCESSING. That is not a
 * flaw to be papered over — it is the unavoidable consequence of two systems without a shared
 * transaction. What matters is that the state is <em>visible</em>: it is not lost, and
 * {@link PaymentReconciliationJob} looks for exactly this. A design that cannot fail this way
 * usually just fails invisibly instead.
 */
@Service
public class PaymentIntentService {

    private static final Logger log = LoggerFactory.getLogger(PaymentIntentService.class);
    private static final int MAX_PAGE_SIZE = 100;

    private final PaymentTransactions transactions;
    private final AcquirerClient acquirerClient;
    private final PaymentIntentRepository paymentIntentRepository;
    private final ChargeRepository chargeRepository;
    private final PaymentMetrics metrics;

    public PaymentIntentService(PaymentTransactions transactions,
                                AcquirerClient acquirerClient,
                                PaymentIntentRepository paymentIntentRepository,
                                ChargeRepository chargeRepository,
                                PaymentMetrics metrics) {
        this.transactions = transactions;
        this.acquirerClient = acquirerClient;
        this.paymentIntentRepository = paymentIntentRepository;
        this.chargeRepository = chargeRepository;
        this.metrics = metrics;
    }

    public PaymentIntent create(String merchantId, Money amount, String customerId,
                                String description, Map<String, String> metadata) {
        PaymentIntent intent = transactions.create(merchantId, amount, customerId, description, metadata);
        metrics.recordCreated(amount);
        return intent;
    }

    /**
     * Confirms a payment: charge the card, record the outcome.
     *
     * @throws Exceptions.CardDeclinedException       the issuer said no (402) — a business answer
     * @throws Exceptions.AcquirerUnavailableException the network did not answer (503) — retryable
     */
    public ConfirmationResult confirm(String merchantId, String paymentIntentId,
                                      String paymentMethodId, String idempotencyKey) {

        PaymentIntent intent = transactions.beginConfirmation(merchantId, paymentIntentId, paymentMethodId);

        // Already settled by an earlier attempt — return what we have rather than charging again.
        if (intent.getStatus() == PaymentIntentStatus.SUCCEEDED) {
            Charge existing = chargeRepository.findByPaymentIntentId(paymentIntentId)
                    .orElseThrow(() -> new IllegalStateException("Succeeded payment without a charge"));
            return new ConfirmationResult(intent, existing);
        }

        AcquirerModels.AuthorizationResponse authorization = metrics.timeAuthorization(() ->
                acquirerClient.authorize(new AcquirerModels.AuthorizationRequest(
                        paymentIntentId,
                        merchantId,
                        intent.getAmount().minorUnits(),
                        intent.getAmount().currency(),
                        paymentMethodId,
                        // Forwarding our key lets the acquirer deduplicate its own retries.
                        idempotencyKey == null ? paymentIntentId : idempotencyKey)));

        if (!authorization.approved()) {
            PaymentIntent failed = transactions.applyDecline(merchantId, paymentIntentId, authorization);
            metrics.recordDeclined(failed.getAmount(), authorization.declineCode());
            throw new Exceptions.CardDeclinedException(
                    authorization.declineCode(),
                    authorization.declineMessage() == null
                            ? "Your card was declined." : authorization.declineMessage());
        }

        Charge charge = transactions.applyApproval(merchantId, paymentIntentId, authorization);
        metrics.recordSucceeded(charge.getAmount(), charge.getFee());

        PaymentIntent settled = paymentIntentRepository.findByIdAndMerchantId(paymentIntentId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("payment intent", paymentIntentId));

        return new ConfirmationResult(settled, charge);
    }

    public PaymentIntent cancel(String merchantId, String paymentIntentId, String reason) {
        PaymentIntent canceled = transactions.cancel(merchantId, paymentIntentId, reason);
        metrics.recordCanceled(canceled.getAmount());
        return canceled;
    }

    @Transactional(readOnly = true)
    public PaymentIntent require(String merchantId, String paymentIntentId) {
        return paymentIntentRepository.findByIdAndMerchantId(paymentIntentId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("payment intent", paymentIntentId));
    }

    @Transactional(readOnly = true)
    public Optional<Charge> chargeFor(String merchantId, String paymentIntentId) {
        return chargeRepository.findByPaymentIntentId(paymentIntentId)
                .filter(charge -> charge.belongsTo(merchantId));
    }

    /**
     * Lists payments for a merchant.
     *
     * <p>The page size is clamped: an unbounded {@code limit} is a denial-of-service vector
     * dressed up as a feature, since a single request can be made to load every row.
     */
    @Transactional(readOnly = true)
    public Page<PaymentIntent> list(String merchantId, PaymentIntentStatus status, int page, int limit) {
        int size = Math.clamp(limit, 1, MAX_PAGE_SIZE);
        PageRequest pageRequest = PageRequest.of(Math.max(page, 0), size);

        return status == null
                ? paymentIntentRepository.findByMerchantIdOrderByCreatedAtDesc(merchantId, pageRequest)
                : paymentIntentRepository.findByMerchantIdAndStatusOrderByCreatedAtDesc(
                        merchantId, status, pageRequest);
    }

    public record ConfirmationResult(PaymentIntent intent, Charge charge) {
    }
}
