package com.paykit.payment.metrics;

import com.paykit.common.money.Money;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

import java.util.function.Supplier;

/**
 * Business metrics, exported to Prometheus via Micrometer.
 *
 * <h3>Counters vs timers vs gauges</h3>
 * <ul>
 *   <li><b>Counter</b> — monotonically increasing. "How many payments succeeded?" Rates are
 *       derived at query time ({@code rate(...[5m])}), so the app never computes them.</li>
 *   <li><b>Timer</b> — count plus distribution. Percentiles are the point: a p99 of 8 seconds
 *       matters even when the mean is 200ms, and the mean will never show it to you.</li>
 *   <li><b>Gauge</b> — a value that goes up and down, sampled on scrape. Queue depth, for
 *       instance. Never use one for something that only increases.</li>
 * </ul>
 *
 * <h3>The cardinality trap</h3>
 * Every distinct combination of tag values creates a separate time series. Tagging by currency
 * (4 values) is fine. Tagging by merchant id, payment id or card number would create millions
 * of series and take the monitoring system down — which is a genuinely common way to cause an
 * outage with observability code. High-cardinality identifiers belong in logs and traces,
 * where they are indexed for lookup rather than aggregated forever.
 */
@Component
public class PaymentMetrics {

    private final MeterRegistry registry;
    private final Timer authorizationTimer;

    public PaymentMetrics(MeterRegistry registry) {
        this.registry = registry;
        this.authorizationTimer = Timer.builder("paykit.acquirer.authorization")
                .description("Time spent waiting for the card network to authorize")
                .publishPercentiles(0.5, 0.95, 0.99)
                .publishPercentileHistogram()
                .register(registry);
    }

    /** Wraps the acquirer call so latency is measured whether it succeeds or throws. */
    public <T> T timeAuthorization(Supplier<T> call) {
        return authorizationTimer.record(call);
    }

    public void recordCreated(Money amount) {
        counter("paykit.payments.created", amount).increment();
    }

    public void recordSucceeded(Money amount, Money fee) {
        counter("paykit.payments.succeeded", amount).increment();
        // Amount counters are in minor units so they stay integers — no floating point in
        // anything that reports money, not even a metric.
        registry.counter("paykit.payments.volume.minor", "currency", amount.currency().name())
                .increment(amount.minorUnits());
        registry.counter("paykit.payments.fees.minor", "currency", fee.currency().name())
                .increment(fee.minorUnits());
    }

    public void recordDeclined(Money amount, String declineCode) {
        // decline_code is a small, closed set from the card networks — safe to tag on.
        registry.counter("paykit.payments.declined",
                "currency", amount.currency().name(),
                "decline_code", declineCode == null ? "unknown" : declineCode).increment();
    }

    public void recordCanceled(Money amount) {
        counter("paykit.payments.canceled", amount).increment();
    }

    public void recordRefunded(Money amount) {
        counter("paykit.payments.refunded", amount).increment();
        registry.counter("paykit.payments.refund.volume.minor", "currency", amount.currency().name())
                .increment(amount.minorUnits());
    }

    private Counter counter(String name, Money amount) {
        return registry.counter(name, "currency", amount.currency().name());
    }
}
