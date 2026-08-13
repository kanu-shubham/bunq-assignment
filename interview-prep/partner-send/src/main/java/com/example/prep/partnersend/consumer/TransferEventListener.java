package com.example.prep.partnersend.consumer;

import com.example.prep.partnersend.outbox.KafkaEventPublisher;
import java.nio.charset.StandardCharsets;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Profile;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * The Kafka end of the consumer: pulls records off the topic and hands them to
 * {@link TransferEventProcessor}, which owns the actual idempotent work.
 *
 * <p>The split is on purpose. This class knows about Kafka; the processor knows about the
 * domain and knows nothing about brokers. So the interesting logic can be tested by calling
 * {@code process()} directly — no broker, no timing, no flakiness — and the integration test
 * only has to prove the wiring. Keeping the transport at the edge is the same instinct as
 * injecting {@code submitFeedback} into the bunq widget rather than calling {@code fetch}
 * inside a component.
 *
 * <p>Note that the listener does not catch exceptions. Letting them propagate is what lets
 * Spring's error handler retry and eventually route to a dead-letter topic — swallowing them
 * here would silently drop events and commit the offset as if all were well.
 */
@Component
@Profile("kafka")
public class TransferEventListener {

    private static final Logger log = LoggerFactory.getLogger(TransferEventListener.class);

    private final TransferEventProcessor processor;

    public TransferEventListener(TransferEventProcessor processor) {
        this.processor = processor;
    }

    @KafkaListener(topics = KafkaEventPublisher.TOPIC, groupId = "${kafka.consumer-group:partner-send}")
    public void onEvent(ConsumerRecord<String, String> record) {
        String eventId = header(record, KafkaEventPublisher.EVENT_ID_HEADER);
        if (eventId == null) {
            // No id means we cannot de-duplicate, so we cannot safely apply it.
            throw new IllegalArgumentException(
                    "Event on partition %d offset %d has no %s header"
                            .formatted(record.partition(), record.offset(),
                                    KafkaEventPublisher.EVENT_ID_HEADER));
        }

        boolean applied = processor.process(eventId, record.value());
        log.debug("event={} partition={} offset={} applied={}",
                eventId, record.partition(), record.offset(), applied);
    }

    private static String header(ConsumerRecord<String, String> record, String name) {
        var header = record.headers().lastHeader(name);
        return header == null ? null : new String(header.value(), StandardCharsets.UTF_8);
    }
}
