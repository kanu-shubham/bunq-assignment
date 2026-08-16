package com.paykit.gateway.config;

import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.cloud.gateway.filter.ratelimit.RateLimiter;
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpStatus;

import java.time.Duration;

/**
 * The routing table, written in Java rather than YAML.
 *
 * <p>WHY JAVA — YAML routes are fine for simple proxying, but here every route needs the same
 * carefully ordered stack of filters. In Java that stack is a method you can name, reuse and
 * read; in YAML it is copy-pasted six times.
 *
 * <p>ANATOMY OF A ROUTE — a predicate ("does this request match?"), a chain of filters
 * ("what do we do to it?"), and a URI ("where does it go?"). {@code lb://} is the important
 * part: it is not a hostname but a <em>service id</em>, resolved through Eureka to a healthy
 * instance by Spring Cloud LoadBalancer.
 */
@Configuration
public class RouteConfig {

    private final RateLimiter<?> redisRateLimiter;
    private final KeyResolver merchantKeyResolver;

    public RouteConfig(RateLimiter<?> redisRateLimiter, KeyResolver merchantKeyResolver) {
        this.redisRateLimiter = redisRateLimiter;
        this.merchantKeyResolver = merchantKeyResolver;
    }

    @Bean
    public RouteLocator paykitRoutes(RouteLocatorBuilder builder) {
        return builder.routes()

                // ---- Auth: exchanging an API key for a JWT. Public, but rate limited hard,
                // ---- because this is the endpoint a credential-stuffing attack would hammer.
                .route("auth-service", r -> r
                        .path("/v1/auth/**")
                        .filters(f -> f
                                .requestRateLimiter(c -> {
                                    c.setRateLimiter(redisRateLimiter);
                                    c.setKeyResolver(merchantKeyResolver);
                                    c.setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
                                })
                                .circuitBreaker(c -> c
                                        .setName("authService")
                                        .setFallbackUri("forward:/__fallback/auth")))
                        .uri("lb://auth-service"))

                // ---- Payments: writes must NOT be retried blindly by the gateway.
                // ---- A retried POST /v1/payment_intents could charge a customer twice; the
                // ---- client's Idempotency-Key is what makes a retry safe, and that decision
                // ---- belongs to the client, not to an intermediary.
                .route("payment-service-writes", r -> r
                        .path("/v1/payment_intents/**", "/v1/refunds/**", "/v1/charges/**")
                        .and().method("POST", "DELETE", "PUT", "PATCH")
                        .filters(f -> f
                                .requestRateLimiter(c -> {
                                    c.setRateLimiter(redisRateLimiter);
                                    c.setKeyResolver(merchantKeyResolver);
                                    c.setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
                                })
                                .circuitBreaker(c -> c
                                        .setName("paymentService")
                                        .setFallbackUri("forward:/__fallback/payments")))
                        .uri("lb://payment-service"))

                // ---- Reads are idempotent by definition, so retrying them is safe and hides
                // ---- a rolling deploy from the caller. Note it retries on a different
                // ---- *instance* each time, courtesy of the load balancer.
                .route("payment-service-reads", r -> r
                        .path("/v1/payment_intents/**", "/v1/refunds/**", "/v1/charges/**")
                        .and().method("GET")
                        .filters(f -> f
                                .requestRateLimiter(c -> {
                                    c.setRateLimiter(redisRateLimiter);
                                    c.setKeyResolver(merchantKeyResolver);
                                    c.setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
                                })
                                .retry(c -> c
                                        .setRetries(2)
                                        .setStatuses(HttpStatus.BAD_GATEWAY, HttpStatus.SERVICE_UNAVAILABLE,
                                                HttpStatus.GATEWAY_TIMEOUT)
                                        .setBackoff(Duration.ofMillis(50), Duration.ofMillis(500), 2, true))
                                .circuitBreaker(c -> c
                                        .setName("paymentService")
                                        .setFallbackUri("forward:/__fallback/payments")))
                        .uri("lb://payment-service"))

                // ---- Ledger: read-only reporting API.
                .route("ledger-service", r -> r
                        .path("/v1/balance/**", "/v1/ledger/**")
                        .filters(f -> f
                                .requestRateLimiter(c -> {
                                    c.setRateLimiter(redisRateLimiter);
                                    c.setKeyResolver(merchantKeyResolver);
                                    c.setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
                                })
                                .circuitBreaker(c -> c
                                        .setName("ledgerService")
                                        .setFallbackUri("forward:/__fallback/ledger")))
                        .uri("lb://ledger-service"))

                // ---- Webhook endpoint management.
                .route("webhook-service", r -> r
                        .path("/v1/webhook_endpoints/**", "/v1/events/**")
                        .filters(f -> f
                                .requestRateLimiter(c -> {
                                    c.setRateLimiter(redisRateLimiter);
                                    c.setKeyResolver(merchantKeyResolver);
                                    c.setStatusCode(HttpStatus.TOO_MANY_REQUESTS);
                                })
                                .circuitBreaker(c -> c
                                        .setName("webhookService")
                                        .setFallbackUri("forward:/__fallback/webhooks")))
                        .uri("lb://webhook-service"))

                .build();
    }
}
