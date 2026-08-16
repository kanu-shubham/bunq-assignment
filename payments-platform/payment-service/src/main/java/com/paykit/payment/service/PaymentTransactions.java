package com.paykit.payment.service;

import com.paykit.common.error.Exceptions;
import com.paykit.common.event.PaymentEvents;
import com.paykit.common.money.Money;
import com.paykit.common.util.Ids;
import com.paykit.payment.acquirer.AcquirerModels;
import com.paykit.payment.config.PaymentProperties;
import com.paykit.payment.domain.Charge;
import com.paykit.payment.domain.PaymentIntent;
import com.paykit.payment.domain.PaymentIntentStatus;
import com.paykit.payment.outbox.OutboxRecorder;
import com.paykit.payment.repository.ChargeRepository;
import com.paykit.payment.repository.PaymentIntentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Map;

/**
 * The short, transactional units of work.
 *
 * <h3>Why this class exists at all</h3>
 * Confirming a payment is "lock the row, call the card network, write the result". Doing all
 * three in one transaction would hold a database row lock for the entire duration of a remote
 * call — hundreds of milliseconds on a good day, and the full timeout on a bad one. Under load
 * that exhausts the connection pool and takes down the service, and it makes the database's
 * health dependent on a third party's.
 *
 * <p>So the work is split: a short transaction to claim the payment, <b>no transaction</b>
 * during the network call, and a second short transaction to record the outcome.
 * {@link PaymentIntentService} orchestrates; this class provides the transactional pieces.
 *
 * <h3>Why they are in a different class</h3>
 * Spring's {@code @Transactional} is implemented with a proxy. If the orchestrator called its
 * own {@code @Transactional} method through {@code this}, the call would never reach the proxy
 * and would silently run with <em>no transaction at all</em>. Separating the beans means the
 * call goes through the container, and the annotation actually takes effect. This is the
 * single most common way {@code @Transactional} is quietly broken in real codebases.
 */
@Service
public class PaymentTransactions {

    private static final Logger log = LoggerFactory.getLogger(PaymentTransactions.class);

    private final PaymentIntentRepository paymentIntentRepository;
    private final ChargeRepository chargeRepository;
    private final OutboxRecorder outboxRecorder;
    private final PaymentProperties properties;

    public PaymentTransactions(PaymentIntentRepository paymentIntentRepository,
                               ChargeRepository chargeRepository,
                               OutboxRecorder outboxRecorder,
                               PaymentProperties properties) {
        this.paymentIntentRepository = paymentIntentRepository;
        this.chargeRepository = chargeRepository;
        this.outboxRecorder = outboxRecorder;
        this.properties = properties;
    }

    /**
     * Creates the intent and records {@code payment_intent.created} in the same transaction.
     * Either both land or neither does — that is the whole point of the outbox.
     */
    @Transactional
    public PaymentIntent create(String merchantId, Money amount, String customerId,
                                String description, Map<String, String> metadata) {
        validateAmount(amount);

        PaymentIntent intent = new PaymentIntent(
                Ids.generate(Ids.PAYMENT_INTENT), merchantId, amount, customerId, description, metadata);
        paymentIntentRepository.save(intent);

        outboxRecorder.record(new PaymentEvents.PaymentIntentCreated(
                Ids.generate(Ids.EVENT), merchantId, intent.getId(), amount,
                customerId, description, intent.getMetadata(), Instant.now()));

        log.info("Created payment intent {} for {} ({})", intent.getId(), amount, merchantId);
        return intent;
    }

    /**
     * Transaction 1 of the confirm flow: take the row lock, validate the transition, move to
     * PROCESSING and commit. After this returns, the payment is visibly in flight to every
     * other instance, so a concurrent confirm is rejected by the state machine rather than by
     * luck.
     */
    @Transactional
    public PaymentIntent beginConfirmation(String merchantId, String paymentIntentId, String paymentMethodId) {
        PaymentIntent intent = paymentIntentRepository.findForUpdate(paymentIntentId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("payment intent", paymentIntentId));

        if (intent.getStatus() == PaymentIntentStatus.SUCCEEDED) {
            // Confirming an already-successful payment is a no-op, not an error: it is what a
            // client does when it never saw the original response.
            return intent;
        }
        intent.confirm(paymentMethodId);
        return intent;
    }

    /**
     * Transaction 2a: the acquirer approved. Create the charge, complete the intent, emit the
     * event — atomically.
     */
    @Transactional
    public Charge applyApproval(String merchantId, String paymentIntentId,
                                AcquirerModels.AuthorizationResponse authorization) {
        PaymentIntent intent = paymentIntentRepository.findForUpdate(paymentIntentId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("payment intent", paymentIntentId));

        // The acquirer's response may arrive after a retry already settled this payment.
        // Returning the existing charge keeps the operation idempotent end to end.
        if (intent.getStatus() == PaymentIntentStatus.SUCCEEDED) {
            return chargeRepository.findByPaymentIntentId(paymentIntentId)
                    .orElseThrow(() -> new IllegalStateException(
                            "Payment intent %s is SUCCEEDED but has no charge".formatted(paymentIntentId)));
        }

        Money amount = intent.getAmount();
        Money fee = properties.feeFor(amount);

        Charge charge = new Charge(
                Ids.generate(Ids.CHARGE), paymentIntentId, merchantId, amount, fee,
                authorization.cardBrand(), authorization.cardLast4(),
                authorization.acquirerReference(), intent.getPaymentMethodId());
        chargeRepository.save(charge);

        intent.markSucceeded(charge.getId());

        outboxRecorder.record(new PaymentEvents.PaymentSucceeded(
                Ids.generate(Ids.EVENT), merchantId, paymentIntentId, charge.getId(),
                amount, fee, intent.getPaymentMethodId(),
                authorization.cardBrand(), authorization.cardLast4(),
                authorization.acquirerReference(), Instant.now()));

        log.info("Payment {} succeeded: charge {} for {} (fee {})",
                paymentIntentId, charge.getId(), amount, fee);
        return charge;
    }

    /** Transaction 2b: the issuer declined. A business outcome, recorded like any other. */
    @Transactional
    public PaymentIntent applyDecline(String merchantId, String paymentIntentId,
                                      AcquirerModels.AuthorizationResponse authorization) {
        PaymentIntent intent = paymentIntentRepository.findForUpdate(paymentIntentId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("payment intent", paymentIntentId));

        if (intent.getStatus() != PaymentIntentStatus.PROCESSING) {
            return intent;
        }

        intent.markFailed(authorization.declineCode(), authorization.declineMessage());

        outboxRecorder.record(new PaymentEvents.PaymentFailed(
                Ids.generate(Ids.EVENT), merchantId, paymentIntentId, intent.getAmount(),
                authorization.declineCode(), authorization.declineMessage(),
                intent.getAttemptCount(), Instant.now()));

        log.info("Payment {} declined: {} ({})",
                paymentIntentId, authorization.declineCode(), authorization.declineMessage());
        return intent;
    }

    @Transactional
    public PaymentIntent cancel(String merchantId, String paymentIntentId, String reason) {
        PaymentIntent intent = paymentIntentRepository.findForUpdate(paymentIntentId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("payment intent", paymentIntentId));

        intent.cancel(reason);

        outboxRecorder.record(new PaymentEvents.PaymentCanceled(
                Ids.generate(Ids.EVENT), merchantId, paymentIntentId,
                intent.getAmount(), reason, Instant.now()));

        return intent;
    }

    private void validateAmount(Money amount) {
        if (amount.isLessThan(properties.minAmount(amount.currency()))) {
            throw new Exceptions.InvalidRequestException(
                    "Amount must be at least %s".formatted(properties.minAmount(amount.currency())), "amount");
        }
        if (amount.isGreaterThan(properties.maxAmount(amount.currency()))) {
            throw new Exceptions.InvalidRequestException(
                    "Amount must not exceed %s".formatted(properties.maxAmount(amount.currency())), "amount");
        }
    }
}
