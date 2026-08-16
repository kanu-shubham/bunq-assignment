package com.paykit.webhook.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.paykit.common.event.EventEnvelope;
import com.paykit.common.event.PaymentEvent;
import com.paykit.common.kafka.Topics;
import com.paykit.common.util.Ids;
import com.paykit.webhook.config.WebhookProperties;
import com.paykit.webhook.domain.ProcessedEvent;
import com.paykit.webhook.domain.WebhookDelivery;
import com.paykit.webhook.domain.WebhookEndpoint;
import com.paykit.webhook.repository.WebhookRepositories.ProcessedEventRepository;
import com.paykit.webhook.repository.WebhookRepositories.WebhookDeliveryRepository;
import com.paykit.webhook.repository.WebhookRepositories.WebhookEndpointRepository;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Turns each payment event into pending delivery rows — one per interested endpoint.
 *
 * <h3>Why queue instead of sending here</h3>
 * Sending inline would tie the Kafka consumer's progress to the slowest merchant server on the
 * internet. One endpoint that takes 30 seconds to respond would stall the partition and delay
 * every other merchant's notifications. Writing a row is fast and predictable; delivery is
 * somebody else's problem (see {@link WebhookSender}).
 *
 * <p>This is the queue-based load-levelling pattern: put a buffer between a fast producer and a
 * slow, unreliable consumer so neither one's problems become the other's.
 */
@Service
public class DeliveryScheduler {

    private static final Logger log = LoggerFactory.getLogger(DeliveryScheduler.class);
    private static final TypeReference<EventEnvelope<PaymentEvent>> ENVELOPE_TYPE =
            new TypeReference<>() {};

    private final ObjectMapper objectMapper;
    private final WebhookEndpointRepository endpointRepository;
    private final WebhookDeliveryRepository deliveryRepository;
    private final ProcessedEventRepository processedEventRepository;
    private final WebhookProperties properties;

    public DeliveryScheduler(ObjectMapper objectMapper,
                             WebhookEndpointRepository endpointRepository,
                             WebhookDeliveryRepository deliveryRepository,
                             ProcessedEventRepository processedEventRepository,
                             WebhookProperties properties) {
        this.objectMapper = objectMapper;
        this.endpointRepository = endpointRepository;
        this.deliveryRepository = deliveryRepository;
        this.processedEventRepository = processedEventRepository;
        this.properties = properties;
    }

    @KafkaListener(topics = Topics.PAYMENT_EVENTS, groupId = Topics.WEBHOOK_GROUP)
    @Transactional
    public void onPaymentEvent(ConsumerRecord<String, String> record) throws Exception {
        EventEnvelope<PaymentEvent> envelope = objectMapper.readValue(record.value(), ENVELOPE_TYPE);

        MDC.put("correlationId", envelope.correlationId() == null ? "-" : envelope.correlationId());
        try {
            if (processedEventRepository.existsById(envelope.eventId())) {
                log.debug("Event {} already scheduled for delivery", envelope.eventId());
                return;
            }

            List<WebhookEndpoint> endpoints = endpointRepository
                    .findByMerchantIdAndStatus(envelope.merchantId(), WebhookEndpoint.Status.ENABLED);

            int scheduled = 0;
            for (WebhookEndpoint endpoint : endpoints) {
                if (!endpoint.wants(envelope.type())) {
                    continue;
                }
                // Belt and braces alongside the dedup table: the unique constraint on
                // (event_id, endpoint_id) is what actually guarantees one delivery per pair.
                if (deliveryRepository.existsByEventIdAndEndpointId(envelope.eventId(), endpoint.getId())) {
                    continue;
                }

                deliveryRepository.save(new WebhookDelivery(
                        Ids.generate("whd"),
                        endpoint.getId(),
                        envelope.merchantId(),
                        envelope.eventId(),
                        envelope.type(),
                        // The merchant receives the event exactly as it was published.
                        record.value(),
                        properties.maxAttempts()));
                scheduled++;
            }

            processedEventRepository.save(new ProcessedEvent(envelope.eventId(), envelope.type()));

            if (scheduled > 0) {
                log.info("Scheduled {} webhook delivery(ies) for {}", scheduled, envelope.type());
            }
        } finally {
            MDC.remove("correlationId");
        }
    }
}
