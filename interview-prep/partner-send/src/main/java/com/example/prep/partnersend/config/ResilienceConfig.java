package com.example.prep.partnersend.config;

import com.example.prep.partnersend.partner.PartnerBankClient;
import com.example.prep.partnersend.partner.ResilientPartnerBankClient;
import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.core.IntervalFunction;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.retry.RetryConfig;
import io.github.resilience4j.timelimiter.TimeLimiter;
import io.github.resilience4j.timelimiter.TimeLimiterConfig;
import java.time.Duration;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;

/**
 * Every resilience knob in one readable place, with the reasoning attached.
 *
 * <p>These are wired by hand rather than through {@code @CircuitBreaker} annotations. In a
 * real service the annotations are fine; here the point is that the machinery stays visible,
 * because "what order are your decorators in?" is a question you want to be able to answer
 * by pointing at code.
 */
@Configuration
public class ResilienceConfig {

    @Bean
    public CircuitBreaker partnerCircuitBreaker() {
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
                // Percentage of recent calls that must fail before opening. 50% is a
                // reasonable default: high enough not to trip on noise, low enough that
                // half the customers are not failing before anything happens.
                .failureRateThreshold(50f)
                // Judge on a rolling window of the last N calls rather than a time window,
                // so behaviour is identical at 10 rps and 10,000 rps.
                .slidingWindowType(CircuitBreakerConfig.SlidingWindowType.COUNT_BASED)
                .slidingWindowSize(20)
                // Never open on a handful of samples: 3 failures out of 4 calls at 04:00 is
                // noise, not an outage. Without this, low-traffic periods trip constantly.
                .minimumNumberOfCalls(10)
                // How long to stay open before testing the water again.
                .waitDurationInOpenState(Duration.ofSeconds(10))
                // In HALF_OPEN, let a few probes through. If they pass, close; if not,
                // re-open. This is what stops a recovering partner from being flattened by
                // the entire backlog the instant it comes back.
                .permittedNumberOfCallsInHalfOpenState(3)
                .automaticTransitionFromOpenToHalfOpenEnabled(true)
                // A call that is merely slow is also a failure — a partner answering in 8s
                // is functionally down, and counting only errors misses the most common
                // real-world outage shape.
                .slowCallRateThreshold(50f)
                .slowCallDurationThreshold(Duration.ofSeconds(2))
                // Do not let a business rejection trip the breaker. The partner is healthy
                // and answering correctly; it is the *payment* that is invalid.
                .ignoreException(ResilienceConfig::isBusinessRejection)
                .build();
        return CircuitBreaker.of("partner-bank", config);
    }

    private static boolean isBusinessRejection(Throwable t) {
        var pbe = ResilientPartnerBankClient.findPartnerBankException(t);
        return pbe != null && !pbe.isRetryable();
    }

    @Bean
    public Retry partnerRetry() {
        RetryConfig config = RetryConfig.custom()
                // 3 total attempts. Retry budgets should be small: each level of a call
                // stack that retries 3 times multiplies with the one below it, so three
                // nested services at 3 attempts each is 27 requests hitting the bottom.
                .maxAttempts(3)
                // Exponential backoff *with jitter*. The exponential part gives a struggling
                // partner room; the jitter is what prevents a thundering herd. Without it,
                // every client that failed at the same instant retries at the same instant,
                // and the recovering service is knocked straight back over. The randomness
                // is the whole point.
                .intervalFunction(IntervalFunction.ofExponentialRandomBackoff(
                        Duration.ofMillis(100), // initial interval
                        2.0,                    // multiplier: 100ms, 200ms, 400ms...
                        0.5))                   // jitter: ±50% of the computed interval
                // Retry only what is worth retrying. Retrying a 400 is pure waste, and
                // under load it is waste aimed at a system already in trouble.
                .retryOnException(ResilientPartnerBankClient::isRetryable)
                .build();
        return Retry.of("partner-bank", config);
    }

    @Bean
    public Bulkhead partnerBulkhead() {
        BulkheadConfig config = BulkheadConfig.custom()
                .maxConcurrentCalls(16)
                // Wait briefly for a slot, then shed. A long queue here just moves the
                // latency somewhere less visible: the caller waits either way, but now the
                // wait is invisible to the metric that would have told you about it.
                .maxWaitDuration(Duration.ofMillis(50))
                .build();
        return Bulkhead.of("partner-bank", config);
    }

    @Bean
    public TimeLimiter partnerTimeLimiter() {
        TimeLimiterConfig config = TimeLimiterConfig.custom()
                // Per attempt. Must be well under the caller's own timeout, or we return
                // an answer to a client that stopped listening. Timeouts should shrink as
                // you go down the stack, never grow.
                .timeoutDuration(Duration.ofSeconds(2))
                .cancelRunningFuture(true)
                .build();
        return TimeLimiter.of("partner-bank", config);
    }

    /**
     * Virtual threads (Java 21) make the "one thread per blocking call" model cheap again —
     * they are scheduled by the JVM rather than the OS, so millions can exist and a blocked
     * one costs almost nothing. Before this, wrapping every call in a future to get a
     * timeout meant paying for a platform thread per in-flight call, which is exactly the
     * pressure that pushed people to reactive programming. Much of that pressure is now gone.
     */
    @Bean(destroyMethod = "shutdown")
    public ExecutorService partnerCallExecutor() {
        return Executors.newVirtualThreadPerTaskExecutor();
    }

    /**
     * {@code @Primary} so anything asking for a {@code PartnerBankClient} gets the protected
     * one. The raw client is still a bean, but you have to ask for it by name — the safe
     * thing is what you get by default.
     */
    @Bean
    @Primary
    public PartnerBankClient resilientPartnerBankClient(
            @org.springframework.beans.factory.annotation.Qualifier("rawPartnerBankClient")
            PartnerBankClient rawPartnerBankClient,
            Bulkhead partnerBulkhead,
            Retry partnerRetry,
            CircuitBreaker partnerCircuitBreaker,
            TimeLimiter partnerTimeLimiter,
            ExecutorService partnerCallExecutor) {
        return new ResilientPartnerBankClient(
                rawPartnerBankClient,
                partnerBulkhead,
                partnerRetry,
                partnerCircuitBreaker,
                partnerTimeLimiter,
                partnerCallExecutor);
    }
}
