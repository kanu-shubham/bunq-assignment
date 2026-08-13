package com.example.prep.partnersend.consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import com.example.prep.partnersend.domain.Money;
import com.example.prep.partnersend.domain.TransferRepository;
import com.example.prep.partnersend.outbox.KafkaEventPublisher;
import com.example.prep.partnersend.outbox.OutboxRepository;
import com.example.prep.partnersend.service.TransferService;
import java.time.Duration;
import java.util.HashSet;
import java.util.Set;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.test.EmbeddedKafkaBroker;
import org.springframework.kafka.test.context.EmbeddedKafka;
import org.springframework.test.context.ActiveProfiles;

/**
 * The full path with a real broker: transfer accepted → outbox → Kafka → consumer → read model.
 *
 * <p>The broker runs in-process ({@code @EmbeddedKafka}), so this needs no Docker and no
 * external service. It is the slowest test in the suite by an order of magnitude, which is
 * itself the lesson: prove properties in fast unit tests, and use integration tests only to
 * prove the wiring you cannot prove any other way.
 */
@SpringBootTest(properties = {
        "spring.kafka.bootstrap-servers=${spring.embedded.kafka.brokers}",
        "spring.kafka.consumer.auto-offset-reset=earliest",
        "outbox.poll-interval-ms=200"
})
@ActiveProfiles("kafka")
@EmbeddedKafka(partitions = 3, topics = KafkaEventPublisher.TOPIC)
class KafkaIntegrationTest {

    @Autowired TransferService transferService;
    @Autowired TransferRepository transfers;
    @Autowired OutboxRepository outbox;
    @Autowired ProcessedEventRepository processedEvents;
    @Autowired PartnerVolumeRepository volumes;
    @Autowired KafkaTemplate<String, String> template;
    @Autowired EmbeddedKafkaBroker broker;

    @BeforeEach
    void clean() {
        outbox.deleteAll();
        transfers.deleteAll();
        processedEvents.deleteAll();
        volumes.deleteAll();
    }

    @Test
    @DisplayName("accepting a transfer eventually updates the read model, end to end")
    void endToEnd() {
        transferService.acceptTransfer("acme", "REF-1", Money.of("GBP", 25_000));

        // "Eventually" is the honest word. The outbox poller, the broker and the consumer are
        // all asynchronous, so the read model is eventually consistent by construction —
        // which is exactly what an event-driven architecture buys and costs.
        await().atMost(Duration.ofSeconds(30)).untilAsserted(() -> {
            var volume = volumes.findById("acme");
            assertThat(volume).isPresent();
            assertThat(volume.get().getTotalMinorUnits()).isEqualTo(25_000);
        });

        assertThat(outbox.findUnpublished()).isEmpty();
    }

    @Test
    @DisplayName("a redelivered event does not double-count")
    void redeliveryIsDeduplicated() {
        transferService.acceptTransfer("acme", "REF-2", Money.of("GBP", 10_000));

        await().atMost(Duration.ofSeconds(30)).untilAsserted(() ->
                assertThat(volumes.findById("acme")).isPresent());

        var event = outbox.findAll().get(0);

        // Publish the identical event again, exactly as an at-least-once broker would after
        // a consumer crashed between applying the event and committing its offset.
        var record = new ProducerRecord<>(
                KafkaEventPublisher.TOPIC, event.getAggregateId(), event.getPayload());
        record.headers().add(KafkaEventPublisher.EVENT_ID_HEADER, event.getEventId().getBytes());
        template.send(record);
        template.flush();

        // Give the consumer time to receive and discard it.
        await().during(Duration.ofSeconds(3)).atMost(Duration.ofSeconds(20)).untilAsserted(() -> {
            var volume = volumes.findById("acme").orElseThrow();
            assertThat(volume.getEventCount()).isEqualTo(1);
            assertThat(volume.getTotalMinorUnits()).isEqualTo(10_000);
        });
    }

    @Test
    @DisplayName("events for one transfer all land on one partition, so their order is kept")
    void keyingByAggregateIdPreservesPerTransferOrdering() {
        // Kafka assigns partitions by hashing the record key. Keying on the transfer id is
        // what buys per-transfer ordering without forcing global ordering — different
        // transfers spread across all 3 partitions and scale out.
        String transferId = "transfer-abc";
        Set<Integer> partitions = new HashSet<>();

        for (int i = 0; i < 6; i++) {
            var result = template.send(new ProducerRecord<>(
                    KafkaEventPublisher.TOPIC, transferId, "{\"seq\":" + i + "}")).join();
            partitions.add(result.getRecordMetadata().partition());
        }

        assertThat(partitions)
                .as("same key must always route to the same partition")
                .hasSize(1);
    }
}
