package com.paykit.payment.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.serialization.StringSerializer;
import org.springframework.boot.autoconfigure.kafka.KafkaProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.core.ProducerFactory;

import java.util.HashMap;
import java.util.Map;

/**
 * Kafka producer settings tuned for a payments system, where durability beats throughput.
 *
 * <p>Every one of these is a default that must be changed to be safe:
 *
 * <dl>
 *   <dt>{@code acks=all}</dt>
 *   <dd>Wait until every in-sync replica has the record. With {@code acks=1} a broker crash
 *       moments after acknowledging loses the event silently — the ledger simply never learns
 *       about a payment that the customer was charged for.</dd>
 *
 *   <dt>{@code enable.idempotence=true}</dt>
 *   <dd>The producer tags records with a sequence number so a retry caused by a lost ack does
 *       not append a duplicate. Without it, "retries > 0" and "no duplicates" are mutually
 *       exclusive.</dd>
 *
 *   <dt>{@code max.in.flight.requests.per.connection=5}</dt>
 *   <dd>With idempotence on, Kafka still guarantees ordering up to 5 in-flight batches. Higher
 *       values silently disable that guarantee.</dd>
 *
 *   <dt>{@code compression.type=snappy}</dt>
 *   <dd>JSON compresses extremely well; this is close to free throughput.</dd>
 * </dl>
 *
 * <p>Note what these settings do <em>not</em> buy: end-to-end exactly-once. The outbox can
 * still re-send after a crash, so consumers deduplicate. See {@code OutboxEvent}.
 */
@Configuration
public class KafkaProducerConfig {

    @Bean
    public ProducerFactory<String, String> producerFactory(KafkaProperties kafkaProperties) {
        Map<String, Object> config = new HashMap<>(kafkaProperties.buildProducerProperties(null));

        config.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        config.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        config.put(ProducerConfig.ACKS_CONFIG, "all");
        config.put(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, true);
        config.put(ProducerConfig.MAX_IN_FLIGHT_REQUESTS_PER_CONNECTION, 5);
        config.put(ProducerConfig.RETRIES_CONFIG, Integer.MAX_VALUE);
        config.put(ProducerConfig.DELIVERY_TIMEOUT_MS_CONFIG, 120_000);
        config.put(ProducerConfig.REQUEST_TIMEOUT_MS_CONFIG, 30_000);
        config.put(ProducerConfig.COMPRESSION_TYPE_CONFIG, "snappy");
        // A small linger lets the producer batch records that arrive close together. 5ms of
        // added latency for a large drop in per-record overhead is a good trade for events.
        config.put(ProducerConfig.LINGER_MS_CONFIG, 5);

        return new DefaultKafkaProducerFactory<>(config);
    }

    @Bean
    public KafkaTemplate<String, String> kafkaTemplate(ProducerFactory<String, String> producerFactory) {
        return new KafkaTemplate<>(producerFactory);
    }

    /** Reuses Spring's configured mapper so events serialise exactly as the REST API does. */
    @Bean
    public ObjectMapper eventObjectMapper(ObjectMapper objectMapper) {
        return objectMapper;
    }
}
