package com.example.prep.partnersend.outbox;

import org.apache.kafka.clients.producer.ProducerRecord;
import org.springframework.context.annotation.Profile;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

/**
 * Sends outbox rows to Kafka. Active only under the {@code kafka} profile, so the default
 * run still uses the logging stand-in and needs no broker.
 *
 * <p>Two decisions here carry the whole event-driven story:
 *
 * <p><b>The key is the aggregate id.</b> Kafka guarantees ordering <em>within a partition</em>,
 * and it assigns partitions by hashing the key. Keying on the transfer id therefore puts every
 * event for one transfer on one partition, in order, while different transfers spread across
 * all partitions and scale out. That is how you get the ordering you actually need without
 * paying for global ordering — which would mean one partition, one consumer, and no scaling.
 *
 * <p><b>The send is synchronous here — deliberately.</b> {@code template.send()} returns a
 * future; if we returned without waiting, the outbox row would be marked published before the
 * broker had acknowledged it, and a broker failure would lose the event silently. Blocking on
 * the result means a failed send throws, the row stays unpublished, and the next poll retries
 * it. Slower, and correct.
 */
@Component
@Profile("kafka")
public class KafkaEventPublisher implements EventPublisher {

    public static final String TOPIC = "transfer-events";

    /** Lets a consumer de-duplicate without parsing the payload. */
    public static final String EVENT_ID_HEADER = "event-id";

    private final KafkaTemplate<String, String> template;

    public KafkaEventPublisher(KafkaTemplate<String, String> template) {
        this.template = template;
    }

    @Override
    public void publish(OutboxEvent event) {
        ProducerRecord<String, String> record =
                new ProducerRecord<>(TOPIC, event.getAggregateId(), event.getPayload());
        record.headers().add(EVENT_ID_HEADER, event.getEventId().getBytes());
        record.headers().add("event-type", event.getEventType().getBytes());

        try {
            // Block until the broker acknowledges. See the class comment: returning early
            // would let us mark the row published before it actually was.
            template.send(record).get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted publishing event " + event.getEventId(), e);
        } catch (Exception e) {
            // Propagating leaves publishedAt null, so OutboxPublisher retries this row.
            throw new IllegalStateException("Failed to publish event " + event.getEventId(), e);
        }
    }
}
