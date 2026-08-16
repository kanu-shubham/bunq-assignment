package com.paykit.ledger.config;

import com.paykit.common.kafka.Topics;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.kafka.KafkaProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.core.ConsumerFactory;
import org.springframework.kafka.core.DefaultKafkaConsumerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.kafka.support.serializer.ErrorHandlingDeserializer;
import org.apache.kafka.common.TopicPartition;
import org.springframework.util.backoff.ExponentialBackOff;

import java.util.HashMap;
import java.util.Map;

/**
 * Consumer settings, and what happens when a message cannot be processed.
 *
 * <h3>Poison messages</h3>
 * A Kafka consumer that throws does not advance its offset, so it re-reads the same record
 * forever. One malformed message stops <em>every</em> message behind it on that partition —
 * an outage caused by a single bad row. The fix is a dead-letter topic: retry a few times, and
 * if it still fails, move the record aside and carry on.
 *
 * <h3>Retry, then park</h3>
 * {@link DefaultErrorHandler} retries in-process with exponential backoff, then hands the
 * record to {@link DeadLetterPublishingRecoverer}, which publishes it to the DLQ with the
 * exception attached as headers. Nothing is lost, nothing blocks, and a human can inspect and
 * replay it later.
 *
 * <h3>Manual offset management</h3>
 * {@code enable.auto.commit=false}. With auto-commit, offsets advance on a timer regardless of
 * whether processing succeeded, so a crash silently skips records. Committing after the work
 * is what makes at-least-once delivery true.
 */
@Configuration
public class KafkaConsumerConfig {

    private static final Logger log = LoggerFactory.getLogger(KafkaConsumerConfig.class);

    @Bean
    public ConsumerFactory<String, String> consumerFactory(KafkaProperties kafkaProperties) {
        Map<String, Object> config = new HashMap<>(kafkaProperties.buildConsumerProperties(null));

        config.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        // A deserialization failure cannot be retried into success. Wrapping the deserializer
        // turns it into a normal error the DLQ path can handle instead of an infinite loop.
        config.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, ErrorHandlingDeserializer.class);
        config.put(ErrorHandlingDeserializer.VALUE_DESERIALIZER_CLASS, StringDeserializer.class);

        config.put(ConsumerConfig.GROUP_ID_CONFIG, Topics.LEDGER_GROUP);
        config.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        // 'earliest' matters for a ledger: a new deployment must read the whole history rather
        // than silently skipping everything published before it started.
        config.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        // Small batches keep each poll's work well inside max.poll.interval.ms. Exceeding it
        // makes the broker assume the consumer died and rebalance mid-batch.
        config.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG, 50);
        config.put(ConsumerConfig.MAX_POLL_INTERVAL_MS_CONFIG, 300_000);
        config.put(ConsumerConfig.SESSION_TIMEOUT_MS_CONFIG, 45_000);

        return new DefaultKafkaConsumerFactory<>(config);
    }

    @Bean
    public ConcurrentKafkaListenerContainerFactory<String, String> kafkaListenerContainerFactory(
            ConsumerFactory<String, String> consumerFactory,
            DefaultErrorHandler errorHandler) {

        ConcurrentKafkaListenerContainerFactory<String, String> factory =
                new ConcurrentKafkaListenerContainerFactory<>();
        factory.setConsumerFactory(consumerFactory);
        factory.setCommonErrorHandler(errorHandler);
        // One thread per partition, up to 3. Going above the partition count just leaves
        // threads idle — partitions, not threads, are the unit of consumer parallelism.
        factory.setConcurrency(3);
        factory.getContainerProperties().setAckMode(
                org.springframework.kafka.listener.ContainerProperties.AckMode.RECORD);
        return factory;
    }

    @Bean
    public DefaultErrorHandler errorHandler(KafkaTemplate<String, String> kafkaTemplate) {
        DeadLetterPublishingRecoverer recoverer = new DeadLetterPublishingRecoverer(
                kafkaTemplate,
                (record, exception) -> {
                    log.error("Sending record from {}-{} offset {} to the DLQ: {}",
                            record.topic(), record.partition(), record.offset(), exception.getMessage());
                    return new TopicPartition(Topics.PAYMENT_EVENTS_DLQ, record.partition());
                });

        ExponentialBackOff backOff = new ExponentialBackOff(500L, 2.0);
        backOff.setMaxElapsedTime(10_000L);

        DefaultErrorHandler handler = new DefaultErrorHandler(recoverer, backOff);
        // Retrying a message that will never deserialize is pure waste — fail it straight to
        // the DLQ.
        handler.addNotRetryableExceptions(
                org.springframework.kafka.support.serializer.DeserializationException.class,
                com.fasterxml.jackson.core.JsonProcessingException.class,
                IllegalArgumentException.class);
        return handler;
    }
}
