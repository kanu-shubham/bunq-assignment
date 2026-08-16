package com.paykit.payment.service;

import com.paykit.payment.domain.PaymentIntent;
import com.paykit.payment.idempotency.IdempotencyService;
import com.paykit.payment.repository.PaymentIntentRepository;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/**
 * The safety net for payments stranded mid-flight.
 *
 * <p>{@link PaymentIntentService#confirm} commits PROCESSING, calls the acquirer, then commits
 * the outcome. Kill the pod in between and the payment sits in PROCESSING forever: the customer
 * may or may not have been charged, and nothing in the request path will ever revisit it.
 *
 * <p>Every distributed system has states like this. What separates a reliable one is not
 * avoiding them — it cannot — but detecting them. This job finds them and makes them loud.
 *
 * <p>A production implementation would go further and <b>query the acquirer</b> for the real
 * status of each stuck payment, then settle it. That is deliberately left as the obvious next
 * step rather than faked here, because guessing would be worse than reporting.
 */
@Component
public class PaymentReconciliationJob {

    private static final Logger log = LoggerFactory.getLogger(PaymentReconciliationJob.class);

    private final PaymentIntentRepository paymentIntentRepository;
    private final IdempotencyService idempotencyService;
    private final Duration stuckThreshold;
    private final Duration idempotencyRetention;
    private final AtomicLong stuckCount = new AtomicLong();

    public PaymentReconciliationJob(PaymentIntentRepository paymentIntentRepository,
                                    IdempotencyService idempotencyService,
                                    MeterRegistry meterRegistry,
                                    @Value("${paykit.reconciliation.stuck-threshold:PT5M}") Duration stuckThreshold,
                                    @Value("${paykit.idempotency.retention:PT24H}") Duration idempotencyRetention) {
        this.paymentIntentRepository = paymentIntentRepository;
        this.idempotencyService = idempotencyService;
        this.stuckThreshold = stuckThreshold;
        this.idempotencyRetention = idempotencyRetention;

        // A GAUGE, because this number goes up and down. It should normally be 0; alert on
        // anything else. This is the single most valuable metric in the service.
        meterRegistry.gauge("paykit.payments.stuck", stuckCount);
    }

    @Scheduled(fixedDelayString = "${paykit.reconciliation.interval-ms:60000}")
    @Transactional(readOnly = true)
    public void findStuckPayments() {
        List<PaymentIntent> stuck = paymentIntentRepository.findStuckInProcessing(
                Instant.now().minus(stuckThreshold));

        stuckCount.set(stuck.size());

        if (!stuck.isEmpty()) {
            log.error("{} payment(s) stuck in PROCESSING for over {} — manual reconciliation "
                            + "with the acquirer required: {}",
                    stuck.size(), stuckThreshold,
                    stuck.stream().map(PaymentIntent::getId).limit(20).toList());
        }
    }

    /**
     * Idempotency keys are only worth keeping for as long as a client might retry.
     * Retention is a deliberate decision, not something to let default to "forever".
     */
    @Scheduled(cron = "${paykit.idempotency.cleanup-cron:0 30 3 * * *}")
    public void purgeExpiredIdempotencyKeys() {
        int purged = idempotencyService.purgeOlderThan(idempotencyRetention);
        if (purged > 0) {
            log.info("Purged {} expired idempotency records", purged);
        }
    }
}
