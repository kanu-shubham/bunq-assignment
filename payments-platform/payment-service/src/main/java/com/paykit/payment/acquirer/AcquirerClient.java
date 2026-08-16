package com.paykit.payment.acquirer;

import com.paykit.common.error.Exceptions;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.github.resilience4j.retry.annotation.Retry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/**
 * Talks to the card network. Every remote call in this system is wrapped exactly like this.
 *
 * <h3>The three patterns, and the order they compose in</h3>
 * <pre>
 *   CircuitBreaker( Retry( TimeLimiter( call ) ) )
 * </pre>
 * <ul>
 *   <li><b>Timeout</b> — a call that never returns is worse than one that fails, because it
 *       holds a thread and a connection forever. Configured on the {@code RestClient} itself.</li>
 *   <li><b>Retry</b> — network blips are common and transient. Retries use exponential backoff
 *       with jitter: without jitter, every client that failed at the same moment retries at the
 *       same moment, and the "thundering herd" finishes off a service that was recovering.</li>
 *   <li><b>Circuit breaker</b> — when failures stop being transient, retrying makes things
 *       worse. The breaker opens and fails fast, giving the downstream room to recover.</li>
 * </ul>
 *
 * <h3>What is NOT retried</h3>
 * Only transport-level failures. A decline is a business answer and is returned as-is; a 4xx
 * means our request was wrong and will be wrong again. Retrying an authorization on an
 * ambiguous timeout is the classic way to double-charge a customer, which is why the
 * idempotency key is forwarded to the acquirer too — so its retry is safe on their side.
 */
@Component
public class AcquirerClient {

    private static final Logger log = LoggerFactory.getLogger(AcquirerClient.class);
    private static final String CIRCUIT = "acquirer";

    private final RestClient restClient;

    public AcquirerClient(RestClient acquirerRestClient) {
        this.restClient = acquirerRestClient;
    }

    /**
     * The annotations are applied by proxies, outermost first: {@code @CircuitBreaker} sees the
     * result of all the retries, so a call that fails three times and then succeeds counts as
     * one success — which is the behaviour you want.
     */
    @CircuitBreaker(name = CIRCUIT, fallbackMethod = "authorizeFallback")
    @Retry(name = CIRCUIT)
    public AcquirerModels.AuthorizationResponse authorize(AcquirerModels.AuthorizationRequest request) {
        log.debug("Authorizing {} for {} {}",
                request.paymentIntentId(), request.amountMinor(), request.currency());

        return restClient.post()
                .uri("/acquirer/v1/authorizations")
                // Passing our idempotency key on to the acquirer makes *their* retry safe too.
                .header("Idempotency-Key", request.idempotencyKey())
                .body(request)
                .retrieve()
                .body(AcquirerModels.AuthorizationResponse.class);
    }

    @CircuitBreaker(name = CIRCUIT, fallbackMethod = "refundFallback")
    @Retry(name = CIRCUIT)
    public AcquirerModels.RefundResponse refund(AcquirerModels.RefundRequest request) {
        return restClient.post()
                .uri("/acquirer/v1/refunds")
                .body(request)
                .retrieve()
                .body(AcquirerModels.RefundResponse.class);
    }

    /**
     * The fallback signature must match the original plus a trailing {@code Throwable};
     * Resilience4j locates it by reflection, so a typo here fails at runtime, not compile time.
     *
     * <p>It deliberately does <b>not</b> invent a result. There is no safe way to guess whether
     * a timed-out authorization reached the card network, so the payment is left unresolved and
     * the caller gets a 503. Reconciliation, not optimism, resolves the ambiguity.
     */
    @SuppressWarnings("unused")
    private AcquirerModels.AuthorizationResponse authorizeFallback(
            AcquirerModels.AuthorizationRequest request, Throwable throwable) {

        log.error("Acquirer authorization failed for {}: {}",
                request.paymentIntentId(), throwable.toString());
        throw new Exceptions.AcquirerUnavailableException(
                "The card network is not responding. The payment has not been completed; "
                        + "retry with the same Idempotency-Key.", throwable);
    }

    @SuppressWarnings("unused")
    private AcquirerModels.RefundResponse refundFallback(
            AcquirerModels.RefundRequest request, Throwable throwable) {

        log.error("Acquirer refund failed for charge {}: {}", request.chargeId(), throwable.toString());
        if (throwable instanceof RestClientException) {
            throw new Exceptions.AcquirerUnavailableException(
                    "The card network is not responding. The refund has not been processed.", throwable);
        }
        throw new Exceptions.AcquirerUnavailableException("Refund could not be processed.", throwable);
    }
}
