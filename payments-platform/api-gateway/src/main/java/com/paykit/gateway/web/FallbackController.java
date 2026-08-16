package com.paykit.gateway.web;

import com.paykit.common.api.ApiError;
import com.paykit.common.api.ErrorCode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Where a request lands when the circuit breaker is open.
 *
 * <h3>Why a circuit breaker exists</h3>
 * Suppose payment-service starts taking 30 seconds to answer. Without a breaker, the gateway
 * keeps sending traffic; every in-flight request holds a connection; the pool exhausts; and
 * now the gateway is down too, taking the ledger and webhook APIs with it. One sick service
 * became a total outage — a <b>cascading failure</b>.
 *
 * <p>The breaker watches the failure rate and, past a threshold, <b>opens</b>: subsequent calls
 * fail instantly here rather than being attempted. That does two things — it answers the caller
 * in milliseconds instead of timing out, and it takes load off the struggling service so it can
 * recover. After a wait it goes <b>half-open</b> and lets a few probes through; if they succeed
 * it closes again, automatically.
 *
 * <p>The response says 503 with {@code Retry-After}, never a fabricated success. Pretending a
 * payment succeeded because the payment service was unreachable is the one unacceptable answer.
 */
@RestController
@RequestMapping("/__fallback")
public class FallbackController {

    private static final Logger log = LoggerFactory.getLogger(FallbackController.class);

    @RequestMapping("/payments")
    public ResponseEntity<ApiError> payments(@RequestHeader(value = "X-Request-Id", required = false) String requestId) {
        return unavailable("payment-service", requestId,
                "Payments are temporarily unavailable. No payment was created or modified — "
                        + "retry with the same Idempotency-Key.");
    }

    @RequestMapping("/auth")
    public ResponseEntity<ApiError> auth(@RequestHeader(value = "X-Request-Id", required = false) String requestId) {
        return unavailable("auth-service", requestId,
                "Authentication is temporarily unavailable. Please retry shortly.");
    }

    @RequestMapping("/ledger")
    public ResponseEntity<ApiError> ledger(@RequestHeader(value = "X-Request-Id", required = false) String requestId) {
        return unavailable("ledger-service", requestId,
                "Balance and ledger reporting is temporarily unavailable. Payments are unaffected.");
    }

    @RequestMapping("/webhooks")
    public ResponseEntity<ApiError> webhooks(@RequestHeader(value = "X-Request-Id", required = false) String requestId) {
        return unavailable("webhook-service", requestId,
                "Webhook management is temporarily unavailable. Queued deliveries are unaffected.");
    }

    private ResponseEntity<ApiError> unavailable(String service, String requestId, String message) {
        log.warn("Circuit breaker open for {} (request {})", service, requestId);
        return ResponseEntity.status(ErrorCode.SERVICE_UNAVAILABLE.status())
                .header("Retry-After", "5")
                .body(ApiError.of(ErrorCode.SERVICE_UNAVAILABLE, message, requestId));
    }
}
