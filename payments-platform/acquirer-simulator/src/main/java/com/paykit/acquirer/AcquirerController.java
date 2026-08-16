package com.paykit.acquirer;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * The simulated card network.
 *
 * <h3>Test payment methods</h3>
 * The behaviour is chosen by the payment method id, exactly the way real sandboxes work:
 * <table border="1">
 *   <caption>Behaviour by token</caption>
 *   <tr><th>Payment method</th><th>Result</th><th>Exercises</th></tr>
 *   <tr><td>{@code pm_card_visa}</td><td>approved</td><td>the happy path</td></tr>
 *   <tr><td>{@code pm_card_mastercard}</td><td>approved</td><td>the happy path</td></tr>
 *   <tr><td>{@code pm_card_declined}</td><td>declined</td><td>402 handling, no breaker trip</td></tr>
 *   <tr><td>{@code pm_card_insufficient_funds}</td><td>declined</td><td>decline codes</td></tr>
 *   <tr><td>{@code pm_card_expired}</td><td>declined</td><td>decline codes</td></tr>
 *   <tr><td>{@code pm_card_slow}</td><td>approved after 3s</td><td>slow-call breaker threshold</td></tr>
 *   <tr><td>{@code pm_card_timeout}</td><td>never answers</td><td>read timeout, retry, fallback</td></tr>
 *   <tr><td>{@code pm_card_error}</td><td>HTTP 500</td><td>retry then circuit breaker</td></tr>
 *   <tr><td>{@code pm_card_flaky}</td><td>fails twice, then works</td><td>retry actually recovering</td></tr>
 * </table>
 */
@RestController
@RequestMapping("/acquirer/v1")
public class AcquirerController {

    private static final Logger log = LoggerFactory.getLogger(AcquirerController.class);

    /** Per-payment attempt counter, so {@code pm_card_flaky} can succeed on the third try. */
    private final Map<String, Integer> attempts = new ConcurrentHashMap<>();

    /**
     * Remembers answers by idempotency key. Real acquirers do this, and it is why forwarding
     * our key downstream (see {@code AcquirerClient}) makes a retry safe on their side too.
     */
    private final Map<String, AuthorizationResponse> byIdempotencyKey = new ConcurrentHashMap<>();

    @PostMapping("/authorizations")
    public ResponseEntity<AuthorizationResponse> authorize(
            @RequestBody AuthorizationRequest request,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey) {

        if (idempotencyKey != null) {
            AuthorizationResponse previous = byIdempotencyKey.get(idempotencyKey);
            if (previous != null) {
                log.info("Replaying authorization for idempotency key {}", idempotencyKey);
                return ResponseEntity.ok(previous);
            }
        }

        String token = request.paymentMethodId() == null ? "" : request.paymentMethodId();
        log.info("Authorizing {} {} with {}", request.amountMinor(), request.currency(), token);

        AuthorizationResponse response = switch (token) {
            case "pm_card_declined" -> declined("card_declined", "Your card was declined.");
            case "pm_card_insufficient_funds" ->
                    declined("insufficient_funds", "Your card has insufficient funds.");
            case "pm_card_expired" -> declined("expired_card", "Your card has expired.");
            case "pm_card_incorrect_cvc" -> declined("incorrect_cvc", "Your card's security code is incorrect.");

            case "pm_card_slow" -> {
                // Slower than the 3s slow-call threshold: the breaker counts this as a failure
                // even though it eventually succeeds.
                sleep(Duration.ofSeconds(3).plusMillis(500));
                yield approved(token);
            }

            case "pm_card_timeout" -> {
                // Longer than the client's read timeout. The client gives up first.
                sleep(Duration.ofSeconds(30));
                yield approved(token);
            }

            case "pm_card_error" -> throw new SimulatedAcquirerOutage();

            case "pm_card_flaky" -> {
                int attempt = attempts.merge(request.paymentIntentId(), 1, Integer::sum);
                if (attempt < 3) {
                    log.info("Flaky card failing attempt {}", attempt);
                    throw new SimulatedAcquirerOutage();
                }
                yield approved(token);
            }

            default -> approved(token);
        };

        if (idempotencyKey != null) {
            byIdempotencyKey.put(idempotencyKey, response);
        }
        return ResponseEntity.ok(response);
    }

    @PostMapping("/refunds")
    public RefundResponse refund(@RequestBody RefundRequest request) {
        log.info("Refunding {} {} against {}",
                request.amountMinor(), request.currency(), request.acquirerReference());

        if ("re_fail".equals(request.reason())) {
            return new RefundResponse(false, null, "refund_declined");
        }
        return new RefundResponse(true, "acqref_" + shortId(), null);
    }

    @GetMapping("/test_cards")
    public List<Map<String, String>> testCards() {
        return List.of(
                Map.of("token", "pm_card_visa", "behaviour", "approved"),
                Map.of("token", "pm_card_mastercard", "behaviour", "approved"),
                Map.of("token", "pm_card_declined", "behaviour", "declined: card_declined"),
                Map.of("token", "pm_card_insufficient_funds", "behaviour", "declined: insufficient_funds"),
                Map.of("token", "pm_card_expired", "behaviour", "declined: expired_card"),
                Map.of("token", "pm_card_incorrect_cvc", "behaviour", "declined: incorrect_cvc"),
                Map.of("token", "pm_card_slow", "behaviour", "approved after 3.5s (trips slow-call threshold)"),
                Map.of("token", "pm_card_timeout", "behaviour", "hangs for 30s (client read timeout)"),
                Map.of("token", "pm_card_error", "behaviour", "HTTP 500 (retry, then circuit breaker)"),
                Map.of("token", "pm_card_flaky", "behaviour", "fails twice then approves (retry recovers)"));
    }

    private AuthorizationResponse approved(String token) {
        String brand = token.contains("mastercard") ? "mastercard" : "visa";
        return new AuthorizationResponse(
                true, "acqref_" + shortId(), null, null, brand, last4(token), "interlink");
    }

    private static AuthorizationResponse declined(String code, String message) {
        return new AuthorizationResponse(false, null, code, message, "visa", "0002", null);
    }

    private static String last4(String token) {
        return token.contains("mastercard") ? "4444" : "4242";
    }

    private static String shortId() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 16);
    }

    private static void sleep(Duration duration) {
        try {
            Thread.sleep(duration.toMillis());
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
        }
    }

    /** Maps to a 500 so the caller's retry and circuit-breaker rules are exercised for real. */
    @org.springframework.web.bind.annotation.ResponseStatus(HttpStatus.INTERNAL_SERVER_ERROR)
    static class SimulatedAcquirerOutage extends RuntimeException {
        SimulatedAcquirerOutage() {
            super("Simulated acquirer outage");
        }
    }

    // ---- wire contract (mirrors payment-service's AcquirerModels) ----

    public record AuthorizationRequest(
            String paymentIntentId, String merchantId, long amountMinor,
            String currency, String paymentMethodId, String idempotencyKey) {
    }

    public record AuthorizationResponse(
            boolean approved, String acquirerReference, String declineCode, String declineMessage,
            String cardBrand, String cardLast4, String network) {
    }

    public record RefundRequest(
            String chargeId, String acquirerReference, long amountMinor, String currency, String reason) {
    }

    public record RefundResponse(boolean approved, String acquirerReference, String failureCode) {
    }
}
