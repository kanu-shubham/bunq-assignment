package com.paykit.webhook.service;

import com.paykit.webhook.config.WebhookProperties;
import com.paykit.webhook.domain.WebhookDelivery;
import com.paykit.webhook.domain.WebhookEndpoint;
import com.paykit.webhook.repository.WebhookRepositories.WebhookDeliveryRepository;
import com.paykit.webhook.repository.WebhookRepositories.WebhookEndpointRepository;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Delivers queued webhooks, with signatures, timeouts and backoff.
 *
 * <h3>What counts as delivered</h3>
 * Any 2xx. Everything else is a failure worth retrying — including 4xx, because a merchant's
 * server returning 404 usually means their deploy is mid-flight, not that the event is invalid.
 * The one exception is {@code 410 Gone}, which is the standard way to say "stop sending".
 *
 * <h3>SSRF, the risk nobody expects</h3>
 * A merchant supplies the destination URL, and this service sits <em>inside</em> the network.
 * Left unchecked, "https://webhooks.example.com" can be swapped for
 * {@code http://169.254.169.254/latest/meta-data/} — the cloud metadata endpoint — and the
 * webhook sender becomes a proxy that reads instance credentials on an attacker's behalf. That
 * is Server-Side Request Forgery, and it is a real, repeatedly-exploited class of bug.
 * {@link UrlValidator} blocks private ranges and non-HTTPS schemes before anything is sent.
 */
@Component
public class WebhookSender {

    private static final Logger log = LoggerFactory.getLogger(WebhookSender.class);

    private final WebhookDeliveryRepository deliveryRepository;
    private final WebhookEndpointRepository endpointRepository;
    private final WebhookSignature signature;
    private final WebhookProperties properties;
    private final WebClient webClient;
    private final Counter deliveredCounter;
    private final Counter failedCounter;

    public WebhookSender(WebhookDeliveryRepository deliveryRepository,
                         WebhookEndpointRepository endpointRepository,
                         WebhookSignature signature,
                         WebhookProperties properties,
                         WebClient webhookWebClient,
                         MeterRegistry meterRegistry) {
        this.deliveryRepository = deliveryRepository;
        this.endpointRepository = endpointRepository;
        this.signature = signature;
        this.properties = properties;
        this.webClient = webhookWebClient;
        this.deliveredCounter = meterRegistry.counter("paykit.webhooks.delivered");
        this.failedCounter = meterRegistry.counter("paykit.webhooks.failed");
    }

    @Scheduled(fixedDelayString = "${paykit.webhooks.poll-interval-ms:1000}")
    @Transactional
    public void deliverDue() {
        List<WebhookDelivery> due = deliveryRepository.claimDue(Instant.now(), properties.batchSize());
        if (due.isEmpty()) {
            return;
        }

        log.debug("Delivering {} webhook(s)", due.size());
        for (WebhookDelivery delivery : due) {
            Optional<WebhookEndpoint> endpoint = endpointRepository.findById(delivery.getEndpointId());
            if (endpoint.isEmpty()) {
                delivery.markAttemptFailed(null, "Endpoint no longer exists", jitter());
                continue;
            }
            attempt(delivery, endpoint.get());
        }
    }

    private void attempt(WebhookDelivery delivery, WebhookEndpoint endpoint) {
        String url = endpoint.getUrl();

        if (!UrlValidator.isSafe(url)) {
            log.error("Refusing to deliver to unsafe URL {} (endpoint {})", url, endpoint.getId());
            delivery.markAttemptFailed(null, "Destination URL is not permitted", jitter());
            endpoint.recordFailure(properties.disableAfterConsecutiveFailures());
            failedCounter.increment();
            return;
        }

        String payload = delivery.getPayload();
        String signatureHeader = signature.sign(payload, endpoint.getSecret(), Instant.now());

        try {
            HttpStatusCode status = webClient.post()
                    .uri(url)
                    .contentType(MediaType.APPLICATION_JSON)
                    .header("Paykit-Signature", signatureHeader)
                    .header("Paykit-Event-Id", delivery.getEventId())
                    .header("Paykit-Event-Type", delivery.getEventType())
                    .header("Paykit-Delivery-Attempt", String.valueOf(delivery.getAttempts() + 1))
                    .header("User-Agent", "Paykit-Webhooks/1.0")
                    .bodyValue(payload)
                    .exchangeToMono(response -> response.releaseBody().thenReturn(response.statusCode()))
                    .block(properties.requestTimeout());

            if (status != null && status.is2xxSuccessful()) {
                delivery.markSucceeded(status.value());
                endpoint.recordSuccess();
                deliveredCounter.increment();
                log.info("Delivered {} to endpoint {} ({})",
                        delivery.getEventType(), endpoint.getId(), status.value());
                return;
            }

            // 410 Gone is the documented way for a merchant to retire an endpoint.
            if (status != null && status.value() == 410) {
                delivery.markAttemptFailed(410, "Endpoint returned 410 Gone", jitter());
                endpoint.recordFailure(1);
                log.info("Endpoint {} returned 410 Gone and has been disabled", endpoint.getId());
                return;
            }

            delivery.markAttemptFailed(
                    status == null ? null : status.value(),
                    "Endpoint returned " + status, jitter());
            endpoint.recordFailure(properties.disableAfterConsecutiveFailures());
            failedCounter.increment();

        } catch (WebClientResponseException ex) {
            delivery.markAttemptFailed(ex.getStatusCode().value(), ex.getMessage(), jitter());
            endpoint.recordFailure(properties.disableAfterConsecutiveFailures());
            failedCounter.increment();
        } catch (Exception ex) {
            // Timeouts, DNS failures, connection refused, TLS errors — all retryable.
            delivery.markAttemptFailed(null, ex.getClass().getSimpleName() + ": " + ex.getMessage(), jitter());
            endpoint.recordFailure(properties.disableAfterConsecutiveFailures());
            failedCounter.increment();
            log.warn("Delivery {} to {} failed: {}", delivery.getId(), url, ex.toString());
        }
    }

    /** 0.0–1.0, turned into a ±50% spread by {@link WebhookDelivery#backoffFor}. */
    private static double jitter() {
        return ThreadLocalRandom.current().nextDouble();
    }
}
