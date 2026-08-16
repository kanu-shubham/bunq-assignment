package com.paykit.webhook.web;

import com.paykit.common.error.Exceptions;
import com.paykit.common.util.Ids;
import com.paykit.webhook.domain.WebhookDelivery;
import com.paykit.webhook.domain.WebhookEndpoint;
import com.paykit.webhook.repository.WebhookRepositories.WebhookDeliveryRepository;
import com.paykit.webhook.repository.WebhookRepositories.WebhookEndpointRepository;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.security.SecureRandom;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;

@RestController
@Tag(name = "Webhooks", description = "Register endpoints and inspect delivery attempts")
public class WebhookController {

    private static final SecureRandom RANDOM = new SecureRandom();

    private final WebhookEndpointRepository endpointRepository;
    private final WebhookDeliveryRepository deliveryRepository;

    public WebhookController(WebhookEndpointRepository endpointRepository,
                             WebhookDeliveryRepository deliveryRepository) {
        this.endpointRepository = endpointRepository;
        this.deliveryRepository = deliveryRepository;
    }

    @PostMapping("/v1/webhook_endpoints")
    @ResponseStatus(HttpStatus.CREATED)
    @Transactional
    @Operation(summary = "Register a webhook endpoint and receive its signing secret")
    public CreatedEndpointResponse create(@RequestHeader("X-Merchant-Id") String merchantId,
                                          @Valid @RequestBody CreateEndpointRequest request) {

        String secret = "whsec_" + HexFormat.of().formatHex(randomBytes(24));

        WebhookEndpoint endpoint = new WebhookEndpoint(
                Ids.generate(Ids.WEBHOOK_ENDPOINT),
                merchantId,
                request.url(),
                secret,
                request.enabledEvents(),
                request.description());

        endpointRepository.save(endpoint);

        // Like an API key, the secret is shown once. Unlike an API key, we must keep a usable
        // copy in order to sign — see the note on WebhookEndpoint.secret.
        return new CreatedEndpointResponse(
                EndpointResponse.from(endpoint),
                secret,
                "Verify every delivery with this secret. See docs/WEBHOOKS.md for the algorithm.");
    }

    @GetMapping("/v1/webhook_endpoints")
    public List<EndpointResponse> list(@RequestHeader("X-Merchant-Id") String merchantId) {
        return endpointRepository.findByMerchantId(merchantId).stream()
                .map(EndpointResponse::from)
                .toList();
    }

    @DeleteMapping("/v1/webhook_endpoints/{endpointId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    @Transactional
    public void delete(@RequestHeader("X-Merchant-Id") String merchantId,
                       @PathVariable String endpointId) {
        WebhookEndpoint endpoint = endpointRepository.findByIdAndMerchantId(endpointId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("webhook endpoint", endpointId));
        endpointRepository.delete(endpoint);
    }

    @PostMapping("/v1/webhook_endpoints/{endpointId}/enable")
    @Transactional
    @Operation(summary = "Re-enable an endpoint that was auto-disabled after repeated failures")
    public EndpointResponse enable(@RequestHeader("X-Merchant-Id") String merchantId,
                                   @PathVariable String endpointId) {
        WebhookEndpoint endpoint = endpointRepository.findByIdAndMerchantId(endpointId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("webhook endpoint", endpointId));
        endpoint.enable();
        return EndpointResponse.from(endpoint);
    }

    @GetMapping("/v1/events")
    @Operation(summary = "Delivery attempts, newest first")
    public DeliveriesResponse deliveries(@RequestHeader("X-Merchant-Id") String merchantId,
                                         @RequestParam(defaultValue = "0") int page,
                                         @RequestParam(defaultValue = "20") int limit) {
        Page<WebhookDelivery> deliveries = deliveryRepository.findByMerchantIdOrderByCreatedAtDesc(
                merchantId, PageRequest.of(Math.max(page, 0), Math.clamp(limit, 1, 100)));

        return new DeliveriesResponse(
                "list",
                deliveries.map(DeliveryResponse::from).getContent(),
                deliveries.hasNext(),
                deliveries.getTotalElements());
    }

    /** Re-queues a delivery that gave up. The manual half of "nothing is ever lost". */
    @PostMapping("/v1/events/{deliveryId}/retry")
    @Transactional
    public DeliveryResponse retry(@RequestHeader("X-Merchant-Id") String merchantId,
                                  @PathVariable String deliveryId) {
        WebhookDelivery delivery = deliveryRepository.findByIdAndMerchantId(deliveryId, merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("webhook delivery", deliveryId));
        delivery.resetForReplay();
        return DeliveryResponse.from(delivery);
    }

    private static byte[] randomBytes(int length) {
        byte[] bytes = new byte[length];
        RANDOM.nextBytes(bytes);
        return bytes;
    }

    // ---- DTOs ----

    public record CreateEndpointRequest(
            @NotBlank @Size(max = 500)
            @Pattern(regexp = "^https?://.+", message = "must be an http(s) URL")
            String url,
            Set<String> enabledEvents,
            @Size(max = 200) String description) {
    }

    public record EndpointResponse(String id, String object, String url, List<String> enabledEvents,
                                   String status, int consecutiveFailures, String description,
                                   Instant createdAt) {

        static EndpointResponse from(WebhookEndpoint endpoint) {
            return new EndpointResponse(
                    endpoint.getId(), "webhook_endpoint", endpoint.getUrl(),
                    List.copyOf(endpoint.eventSet()), endpoint.getStatus().name().toLowerCase(),
                    endpoint.getConsecutiveFailures(), endpoint.getDescription(),
                    endpoint.getCreatedAt());
        }
    }

    public record CreatedEndpointResponse(EndpointResponse endpoint, String secret, String note) {
    }

    public record DeliveryResponse(String id, String object, String endpointId, String eventId,
                                   String eventType, String status, int attempts, int maxAttempts,
                                   Integer responseStatus, String lastError, Instant nextAttemptAt,
                                   Instant deliveredAt, Instant createdAt) {

        static DeliveryResponse from(WebhookDelivery delivery) {
            return new DeliveryResponse(
                    delivery.getId(), "webhook_delivery", delivery.getEndpointId(),
                    delivery.getEventId(), delivery.getEventType(),
                    delivery.getStatus().name().toLowerCase(), delivery.getAttempts(),
                    delivery.getMaxAttempts(), delivery.getResponseStatus(), delivery.getLastError(),
                    delivery.getNextAttemptAt(), delivery.getDeliveredAt(), delivery.getCreatedAt());
        }
    }

    public record DeliveriesResponse(String object, List<DeliveryResponse> data,
                                     boolean hasMore, long totalCount) {
    }
}
