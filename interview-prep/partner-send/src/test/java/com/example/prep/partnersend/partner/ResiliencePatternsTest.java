package com.example.prep.partnersend.partner;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.example.prep.partnersend.domain.Money;
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
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * Each test isolates one stability pattern and demonstrates the behaviour you would claim
 * for it in an interview. Timings are kept deliberately short so the suite stays fast.
 */
class ResiliencePatternsTest {

    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();

    private static final PaymentInstruction INSTRUCTION =
            PaymentInstruction.forTransfer("t-1", "GB33BUKB20201555555555", Money.of("GBP", 100));

    @AfterEach
    void tearDown() {
        executor.shutdownNow();
    }

    // ---------------------------------------------------------------- helpers

    private ResilientPartnerBankClient client(
            PartnerBankClient delegate, Retry retry, CircuitBreaker breaker, TimeLimiter limiter) {
        return new ResilientPartnerBankClient(
                delegate,
                Bulkhead.of("test", BulkheadConfig.custom().maxConcurrentCalls(64).build()),
                retry,
                breaker,
                limiter,
                executor);
    }

    private static Retry retry(int attempts) {
        return Retry.of("test", RetryConfig.custom()
                .maxAttempts(attempts)
                .intervalFunction(IntervalFunction.of(Duration.ofMillis(5)))
                .retryOnException(ResilientPartnerBankClient::isRetryable)
                .build());
    }

    private static CircuitBreaker openBreaker(int windowSize, int minCalls) {
        return CircuitBreaker.of("test", CircuitBreakerConfig.custom()
                .failureRateThreshold(50f)
                .slidingWindowType(CircuitBreakerConfig.SlidingWindowType.COUNT_BASED)
                .slidingWindowSize(windowSize)
                .minimumNumberOfCalls(minCalls)
                .waitDurationInOpenState(Duration.ofMillis(200))
                .permittedNumberOfCallsInHalfOpenState(2)
                .build());
    }

    private static CircuitBreaker alwaysClosed() {
        return CircuitBreaker.of("test", CircuitBreakerConfig.custom()
                .minimumNumberOfCalls(Integer.MAX_VALUE) // never gathers enough to trip
                .build());
    }

    private static TimeLimiter limiter(Duration timeout) {
        return TimeLimiter.of("test",
                TimeLimiterConfig.custom().timeoutDuration(timeout).cancelRunningFuture(true).build());
    }

    // ---------------------------------------------------------------- retry

    @Test
    @DisplayName("a transient failure is retried and the second attempt succeeds")
    void retriesTransientFailures() {
        AtomicInteger calls = new AtomicInteger();
        PartnerBankClient flaky = instruction -> {
            if (calls.incrementAndGet() == 1) {
                throw PartnerBankException.unavailable("503");
            }
            return PartnerAck.accepted("SCHEME-1");
        };

        var result = client(flaky, retry(3), alwaysClosed(), limiter(Duration.ofSeconds(2)))
                .submit(INSTRUCTION);

        assertThat(result.accepted()).isTrue();
        assertThat(calls.get()).isEqualTo(2);
    }

    @Test
    @DisplayName("a business rejection is NOT retried — the answer will not change")
    void doesNotRetryRejections() {
        AtomicInteger calls = new AtomicInteger();
        PartnerBankClient rejecting = instruction -> {
            calls.incrementAndGet();
            throw PartnerBankException.rejected("Beneficiary account closed");
        };

        assertThatThrownBy(() ->
                client(rejecting, retry(3), alwaysClosed(), limiter(Duration.ofSeconds(2)))
                        .submit(INSTRUCTION))
                .isInstanceOf(PartnerBankException.class)
                .hasMessageContaining("closed");

        // The single most common resilience bug is retrying this. It cannot succeed, and
        // under load it is wasted traffic aimed at a system already saying no.
        assertThat(calls.get()).as("rejections must be attempted exactly once").isEqualTo(1);
    }

    @Test
    void givesUpAfterMaxAttempts() {
        AtomicInteger calls = new AtomicInteger();
        PartnerBankClient down = instruction -> {
            calls.incrementAndGet();
            throw PartnerBankException.unavailable("503");
        };

        assertThatThrownBy(() ->
                client(down, retry(3), alwaysClosed(), limiter(Duration.ofSeconds(2))).submit(INSTRUCTION))
                .isInstanceOf(PartnerBankException.class);

        assertThat(calls.get()).isEqualTo(3);
    }

    @Test
    @DisplayName("the retry key is stable across attempts, so the partner can de-duplicate")
    void idempotencyKeyDoesNotChangeBetweenAttempts() {
        var seen = java.util.Collections.synchronizedSet(new java.util.HashSet<String>());
        AtomicInteger calls = new AtomicInteger();
        PartnerBankClient recording = instruction -> {
            seen.add(instruction.idempotencyKey());
            if (calls.incrementAndGet() < 3) {
                throw PartnerBankException.unavailable("503");
            }
            return PartnerAck.accepted("SCHEME-1");
        };

        client(recording, retry(3), alwaysClosed(), limiter(Duration.ofSeconds(2))).submit(INSTRUCTION);

        // Three attempts, one key. A key regenerated per attempt would turn a network
        // blip into three separate payments at the partner's end.
        assertThat(calls.get()).isEqualTo(3);
        assertThat(seen).hasSize(1);
    }

    // ---------------------------------------------------------- circuit breaker

    @Test
    @DisplayName("the breaker opens after enough failures and then fails fast")
    void breakerOpensAndShedsLoad() {
        AtomicInteger calls = new AtomicInteger();
        PartnerBankClient down = instruction -> {
            calls.incrementAndGet();
            throw PartnerBankException.unavailable("503");
        };

        CircuitBreaker breaker = openBreaker(4, 4);
        var subject = client(down, retry(1), breaker, limiter(Duration.ofSeconds(2)));

        for (int i = 0; i < 4; i++) {
            assertThatThrownBy(() -> subject.submit(INSTRUCTION))
                    .isInstanceOf(PartnerBankException.class);
        }
        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.OPEN);

        int callsBeforeOpen = calls.get();

        // Further requests are rejected without touching the partner at all — that is the
        // whole value: a struggling downstream gets breathing room instead of a pile-on.
        assertThatThrownBy(() -> subject.submit(INSTRUCTION))
                .isInstanceOf(PartnerBankException.class)
                .hasMessageContaining("Circuit is open");

        assertThat(calls.get()).as("no call reaches the partner while open").isEqualTo(callsBeforeOpen);
    }

    @Test
    @DisplayName("after the wait, the breaker half-opens and closes again once calls succeed")
    void breakerRecovers() throws Exception {
        AtomicInteger failuresLeft = new AtomicInteger(4);
        PartnerBankClient recovering = instruction -> {
            if (failuresLeft.getAndDecrement() > 0) {
                throw PartnerBankException.unavailable("503");
            }
            return PartnerAck.accepted("SCHEME-" + UUID.randomUUID());
        };

        CircuitBreaker breaker = openBreaker(4, 4);
        var subject = client(recovering, retry(1), breaker, limiter(Duration.ofSeconds(2)));

        for (int i = 0; i < 4; i++) {
            assertThatThrownBy(() -> subject.submit(INSTRUCTION)).isInstanceOf(PartnerBankException.class);
        }
        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.OPEN);

        // waitDurationInOpenState elapses
        Thread.sleep(250);
        breaker.transitionToHalfOpenState();
        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.HALF_OPEN);

        // Only a couple of probes are admitted — a recovering partner is not handed the
        // entire backlog the instant it comes back up.
        subject.submit(INSTRUCTION);
        subject.submit(INSTRUCTION);

        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.CLOSED);
    }

    @Test
    @DisplayName("business rejections do not trip the breaker — the partner is healthy")
    void rejectionsAreIgnoredByTheBreaker() {
        PartnerBankClient rejecting = instruction -> {
            throw PartnerBankException.rejected("Invalid IBAN");
        };

        CircuitBreaker breaker = CircuitBreaker.of("test", CircuitBreakerConfig.custom()
                .failureRateThreshold(50f)
                .slidingWindowType(CircuitBreakerConfig.SlidingWindowType.COUNT_BASED)
                .slidingWindowSize(4)
                .minimumNumberOfCalls(4)
                .ignoreException(t -> {
                    var pbe = ResilientPartnerBankClient.findPartnerBankException(t);
                    return pbe != null && !pbe.isRetryable();
                })
                .build());

        var subject = client(rejecting, retry(1), breaker, limiter(Duration.ofSeconds(2)));
        for (int i = 0; i < 6; i++) {
            assertThatThrownBy(() -> subject.submit(INSTRUCTION)).isInstanceOf(PartnerBankException.class);
        }

        // Ten thousand invalid IBANs from one buggy partner must not cut off every other
        // partner's payments. Only infrastructure failures belong in the breaker's window.
        assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.CLOSED);
    }

    // ------------------------------------------------------------- time limiter

    @Test
    @DisplayName("a hung partner is abandoned at the deadline rather than waited on forever")
    void timeLimiterCutsOffSlowCalls() {
        PartnerBankClient hung = instruction -> {
            try {
                Thread.sleep(5_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return PartnerAck.accepted("SCHEME-1");
        };

        long start = System.nanoTime();
        assertThatThrownBy(() ->
                client(hung, retry(1), alwaysClosed(), limiter(Duration.ofMillis(150))).submit(INSTRUCTION))
                .isInstanceOf(PartnerBankException.class);
        long elapsedMs = (System.nanoTime() - start) / 1_000_000;

        // An unbounded wait is the one outcome a distributed system must never have:
        // it is how a single slow dependency exhausts a thread pool and takes down
        // endpoints that have nothing to do with it.
        assertThat(elapsedMs).isLessThan(2_000);
    }

    @Test
    @DisplayName("the timeout is per attempt, so retries each get a full budget")
    void timeoutAppliesPerAttemptNotPerRequest() {
        AtomicInteger calls = new AtomicInteger();
        PartnerBankClient slowThenFast = instruction -> {
            if (calls.incrementAndGet() == 1) {
                try {
                    Thread.sleep(5_000);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
            return PartnerAck.accepted("SCHEME-1");
        };

        // The first attempt is cut off at 150ms; the second still gets to run and succeed.
        // Had the deadline covered the whole request, the retry would never have happened.
        var ack = client(slowThenFast, retry(3), alwaysClosed(), limiter(Duration.ofMillis(150)))
                .submit(INSTRUCTION);

        assertThat(ack.accepted()).isTrue();
        assertThat(calls.get()).isEqualTo(2);
    }

    // ----------------------------------------------------------------- bulkhead

    @Test
    @DisplayName("the bulkhead sheds load instead of letting one partner drain the pool")
    void bulkheadLimitsConcurrency() throws Exception {
        int limit = 2;
        CountDownLatch hold = new CountDownLatch(1);
        CountDownLatch started = new CountDownLatch(limit);

        PartnerBankClient blocking = instruction -> {
            started.countDown();
            try {
                hold.await(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return PartnerAck.accepted("SCHEME-1");
        };

        var subject = new ResilientPartnerBankClient(
                blocking,
                Bulkhead.of("test", BulkheadConfig.custom()
                        .maxConcurrentCalls(limit)
                        .maxWaitDuration(Duration.ofMillis(10))
                        .build()),
                retry(1),
                alwaysClosed(),
                limiter(Duration.ofSeconds(5)),
                executor);

        try (ExecutorService callers = Executors.newFixedThreadPool(limit + 1)) {
            for (int i = 0; i < limit; i++) {
                callers.submit(() -> subject.submit(INSTRUCTION));
            }
            assertThat(started.await(5, TimeUnit.SECONDS)).isTrue();

            // The slots are full. The next caller is refused immediately rather than
            // queueing up behind a partner that may never answer.
            assertThatThrownBy(() -> subject.submit(INSTRUCTION))
                    .isInstanceOf(PartnerBankException.class)
                    .hasMessageContaining("shedding load");

            hold.countDown();
        }
    }

    // --------------------------------------------------------------- classification

    @Test
    void retryClassificationSurvivesFutureWrapping() {
        // CompletableFuture nests the original exception inside an ExecutionException.
        // If the predicate did not unwrap, every failure would look unclassifiable and
        // either everything or nothing would be retried.
        Throwable wrapped = new java.util.concurrent.ExecutionException(
                new RuntimeException(PartnerBankException.unavailable("503")));
        assertThat(ResilientPartnerBankClient.isRetryable(wrapped)).isTrue();

        Throwable wrappedRejection = new java.util.concurrent.ExecutionException(
                PartnerBankException.rejected("Invalid IBAN"));
        assertThat(ResilientPartnerBankClient.isRetryable(wrappedRejection)).isFalse();

        assertThat(ResilientPartnerBankClient.isRetryable(
                new java.util.concurrent.TimeoutException("deadline"))).isTrue();
    }
}
