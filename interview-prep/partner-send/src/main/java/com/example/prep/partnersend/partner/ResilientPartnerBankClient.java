package com.example.prep.partnersend.partner;

import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.retry.Retry;
import io.github.resilience4j.timelimiter.TimeLimiter;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.function.Supplier;

/**
 * Wraps a raw {@link PartnerBankClient} in the four classic stability patterns.
 *
 * <h2>The order of the layers, and why</h2>
 *
 * <pre>
 *   Bulkhead ( Retry ( CircuitBreaker ( TimeLimiter ( actual call ) ) ) )
 *   outermost                                          innermost
 * </pre>
 *
 * <p>Every one of those positions is a decision an interviewer can probe:
 *
 * <p><b>TimeLimiter innermost — per attempt, not per request.</b> Each attempt gets its own
 * deadline. If the timeout sat outside the retry, one slow attempt would eat the entire
 * budget and the retries would never get to run. (The genuinely correct production answer
 * is <em>both</em>: a per-attempt timeout plus an overall deadline propagated from the
 * caller, so a request that has already burned its client's patience stops retrying. See
 * the note on deadline propagation at the bottom.)
 *
 * <p><b>CircuitBreaker inside Retry.</b> This is the contested one, so know the trade:
 *
 * <ul>
 *   <li><i>Retry outside the breaker</i> (what this does): the breaker records every
 *       individual attempt, so it sees the true failure rate and trips quickly. Once open,
 *       the remaining retries fail instantly with {@link CallNotPermittedException} instead
 *       of hammering a downstream that is already unwell.</li>
 *   <li><i>Retry inside the breaker</i>: the breaker sees one outcome per logical request.
 *       Its statistics are cleaner conceptually, but it needs many more failures to trip and
 *       the retries keep flowing at a struggling partner in the meantime.</li>
 * </ul>
 *
 * <p>For a downstream you can overwhelm — which a partner bank very much is — recording each
 * attempt and tripping early is the safer default.
 *
 * <p><b>Bulkhead outermost.</b> It caps how many transfers can be in flight to this partner
 * at once, and that cap has to include time spent waiting between retries. The failure it
 * prevents is the one that takes down the whole service: one slow partner otherwise absorbs
 * every thread in the pool, and the endpoints that have nothing to do with that partner
 * start timing out too. The bulkhead confines the damage to the partner causing it.
 *
 * <p>Together these implement the same principle: <b>fail fast, in one place, on purpose</b>.
 * An unbounded wait is the one outcome a distributed system must never have.
 */
public class ResilientPartnerBankClient implements PartnerBankClient {

    private final PartnerBankClient delegate;
    private final Bulkhead bulkhead;
    private final Retry retry;
    private final CircuitBreaker circuitBreaker;
    private final TimeLimiter timeLimiter;
    private final ExecutorService callExecutor;

    public ResilientPartnerBankClient(
            PartnerBankClient delegate,
            Bulkhead bulkhead,
            Retry retry,
            CircuitBreaker circuitBreaker,
            TimeLimiter timeLimiter,
            ExecutorService callExecutor) {
        this.delegate = delegate;
        this.bulkhead = bulkhead;
        this.retry = retry;
        this.circuitBreaker = circuitBreaker;
        this.timeLimiter = timeLimiter;
        this.callExecutor = callExecutor;
    }

    @Override
    public PartnerAck submit(PaymentInstruction instruction) {
        // The call runs on a separate thread because that is the only way to impose a
        // deadline on code that might block in a socket read: you cannot interrupt a
        // blocking I/O call from outside, you can only stop waiting for it. Note the
        // consequence — an abandoned call is still running at the partner's end. Hence,
        // again, idempotency keys.
        Supplier<Future<PartnerAck>> futureSupplier =
                () -> CompletableFuture.supplyAsync(() -> delegate.submit(instruction), callExecutor);

        Callable<PartnerAck> timed = timeLimiter.decorateFutureSupplier(futureSupplier);
        Callable<PartnerAck> breakered = CircuitBreaker.decorateCallable(circuitBreaker, timed);
        Callable<PartnerAck> retried = Retry.decorateCallable(retry, breakered);
        Callable<PartnerAck> bulkheaded = Bulkhead.decorateCallable(bulkhead, retried);

        try {
            return bulkheaded.call();
        } catch (BulkheadFullException e) {
            // Shedding load is a deliberate, healthy response, not an error to hide.
            throw PartnerBankException.unavailable(
                    "Too many concurrent requests to partner; shedding load");
        } catch (CallNotPermittedException e) {
            throw PartnerBankException.unavailable("Circuit is open for partner");
        } catch (Exception e) {
            throw asPartnerBankException(e);
        }
    }

    /**
     * Unwraps the layers of wrapping that {@code CompletableFuture} adds so the original
     * classification survives. Without this, every failure arrives as an
     * {@code ExecutionException} — {@code isRetryable()} is lost and the retry predicate
     * cannot tell a 503 from a malformed IBAN.
     */
    static PartnerBankException asPartnerBankException(Throwable t) {
        PartnerBankException found = findPartnerBankException(t);
        if (found != null) {
            return found;
        }
        if (t instanceof java.util.concurrent.TimeoutException) {
            return PartnerBankException.timeout("Partner call exceeded its deadline");
        }
        return new PartnerBankException("Partner call failed: " + t, false, t);
    }

    /** Walks the cause chain, since the real exception can be nested several deep. */
    public static PartnerBankException findPartnerBankException(Throwable t) {
        Throwable current = t;
        while (current != null) {
            if (current instanceof PartnerBankException pbe) {
                return pbe;
            }
            if (current.getCause() == current) {
                break;
            }
            current = current.getCause();
        }
        return null;
    }

    /**
     * The retry predicate. Only genuinely retryable failures are retried; a rejection is
     * returned to the caller immediately.
     *
     * <p>A {@code TimeoutException} counts as retryable — see the discussion of ambiguous
     * outcomes on {@link PartnerBankException}.
     */
    public static boolean isRetryable(Throwable t) {
        if (t instanceof CallNotPermittedException) {
            return false; // circuit is open; retrying is pointless until it half-opens
        }
        PartnerBankException pbe = findPartnerBankException(t);
        if (pbe != null) {
            return pbe.isRetryable();
        }
        return hasCauseOfType(t, java.util.concurrent.TimeoutException.class);
    }

    private static boolean hasCauseOfType(Throwable t, Class<? extends Throwable> type) {
        Throwable current = t;
        while (current != null) {
            if (type.isInstance(current)) {
                return true;
            }
            if (current.getCause() == current) {
                break;
            }
            current = current.getCause();
        }
        return false;
    }
}
