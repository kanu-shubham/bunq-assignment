package com.paykit.payment.service;

import com.paykit.common.error.Exceptions;
import com.paykit.common.event.PaymentEvents;
import com.paykit.common.money.Money;
import com.paykit.common.util.Ids;
import com.paykit.payment.acquirer.AcquirerClient;
import com.paykit.payment.acquirer.AcquirerModels;
import com.paykit.payment.domain.Charge;
import com.paykit.payment.domain.Refund;
import com.paykit.payment.metrics.PaymentMetrics;
import com.paykit.payment.outbox.OutboxRecorder;
import com.paykit.payment.repository.ChargeRepository;
import com.paykit.payment.repository.RefundRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;

/**
 * Sends money back.
 *
 * <h3>The invariant that matters</h3>
 * The sum of refunds against a charge must never exceed the charge. Two concurrent partial
 * refunds are the obvious way to break it: both read "0 refunded so far", both decide there is
 * room, both commit. {@code findForUpdate} takes a row lock so the check-then-act sequence is
 * serialised, and {@link Charge#recordRefund} enforces the rule inside the entity so no caller
 * can skip it.
 *
 * <h3>Fees</h3>
 * The platform fee is refunded proportionally: refund half the payment, get half the fee back.
 * Some processors keep the whole fee on a refund — a policy choice, but one that must be
 * deliberate rather than an accident of where the code was written.
 */
@Service
public class RefundService {

    private static final Logger log = LoggerFactory.getLogger(RefundService.class);

    private final ChargeRepository chargeRepository;
    private final RefundRepository refundRepository;
    private final AcquirerClient acquirerClient;
    private final OutboxRecorder outboxRecorder;
    private final PaymentMetrics metrics;

    public RefundService(ChargeRepository chargeRepository,
                         RefundRepository refundRepository,
                         AcquirerClient acquirerClient,
                         OutboxRecorder outboxRecorder,
                         PaymentMetrics metrics) {
        this.chargeRepository = chargeRepository;
        this.refundRepository = refundRepository;
        this.acquirerClient = acquirerClient;
        this.outboxRecorder = outboxRecorder;
        this.metrics = metrics;
    }

    /**
     * A full or partial refund.
     *
     * <p>Unlike {@code confirm}, this keeps the acquirer call inside the transaction. The
     * trade-off is deliberate: refund volume is a fraction of payment volume, and holding the
     * lock across the call removes any window in which two refunds could both be authorised
     * for the same money. Correctness is worth more than throughput on this path — but it is
     * a choice, and it would be the wrong one at authorisation volume.
     */
    @Transactional
    public Refund refund(String merchantId, String chargeId, Money requestedAmount, Refund.Reason reason) {
        Charge charge = chargeRepository.findForUpdate(chargeId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("charge", chargeId));

        // Omitting the amount means "refund whatever is left", which is what most callers want
        // and removes a race between reading the balance and asking for it.
        Money amount = requestedAmount == null ? charge.getRefundable() : requestedAmount;

        if (charge.isFullyRefunded()) {
            throw new Exceptions.InvalidRequestException(
                    "Charge %s has already been fully refunded".formatted(chargeId), "charge");
        }

        // Throws if it would over-refund or mixes currencies. The entity owns the rule.
        charge.recordRefund(amount);

        Money feeShare = charge.feeShareFor(amount);

        AcquirerModels.RefundResponse response = acquirerClient.refund(new AcquirerModels.RefundRequest(
                chargeId, charge.getAcquirerReference(), amount.minorUnits(),
                amount.currency(), reason == null ? null : reason.name()));

        if (!response.approved()) {
            // Unchecked exception -> Spring rolls the transaction back, so recordRefund above
            // is undone and the charge is untouched. This is the rollback semantics that make
            // "just throw" a safe way to abort a business operation.
            throw new Exceptions.InvalidRequestException(
                    "The card network declined the refund (%s)".formatted(response.failureCode()));
        }

        Refund refund = new Refund(Ids.generate(Ids.REFUND), charge, amount, feeShare, reason);
        refundRepository.save(refund);

        outboxRecorder.record(new PaymentEvents.RefundSucceeded(
                Ids.generate(Ids.EVENT), merchantId, charge.getPaymentIntentId(),
                refund.getId(), chargeId, amount, feeShare,
                refund.getReason().name(), Instant.now()));

        metrics.recordRefunded(amount);
        log.info("Refunded {} of charge {} (fee returned {})", amount, chargeId, feeShare);

        return refund;
    }

    @Transactional(readOnly = true)
    public Refund require(String merchantId, String refundId) {
        return refundRepository.findByIdAndMerchantId(refundId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("refund", refundId));
    }

    @Transactional(readOnly = true)
    public List<Refund> listForCharge(String merchantId, String chargeId) {
        Charge charge = chargeRepository.findByIdAndMerchantId(chargeId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("charge", chargeId));
        return refundRepository.findByChargeIdOrderByCreatedAtDesc(charge.getId());
    }
}
