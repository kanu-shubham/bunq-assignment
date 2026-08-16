package com.paykit.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;

/**
 * The one door into the platform.
 *
 * <p>Everything the outside world sends arrives here first. Putting these concerns in the
 * gateway means the six services behind it never re-implement them:
 *
 * <ul>
 *   <li><b>Routing</b> — {@code /v1/payment_intents/**} goes to payment-service. See {@code GatewayRoutesConfig}.</li>
 *   <li><b>Load balancing</b> — {@code lb://payment-service} resolves through Eureka to a live instance.</li>
 *   <li><b>Authentication</b> — one JWT/API-key check at the edge; internal services trust the
 *       {@code X-Merchant-Id} header the gateway stamps on. See {@code AuthenticationFilter}.</li>
 *   <li><b>Rate limiting</b> — a Redis token bucket per merchant, so one noisy tenant cannot
 *       starve the rest. See {@code RateLimiterConfig}.</li>
 *   <li><b>Circuit breaking</b> — when payment-service is unhealthy, fail fast with a useful
 *       503 instead of piling up threads. See {@code FallbackController}.</li>
 *   <li><b>Observability</b> — every request gets a correlation id that follows it everywhere.</li>
 * </ul>
 *
 * <p>NOTE — this is a <b>WebFlux</b> (reactive, non-blocking) application, not servlet-based.
 * A gateway spends nearly all its time waiting on the network, so a handful of event-loop
 * threads multiplex thousands of in-flight requests. Never call a blocking API from a filter
 * here: blocking an event-loop thread stalls every request sharing it.
 */
@SpringBootApplication
@EnableDiscoveryClient
@ConfigurationPropertiesScan
public class ApiGatewayApplication {

    public static void main(String[] args) {
        SpringApplication.run(ApiGatewayApplication.class, args);
    }
}
