package com.example.prep.partnersend.consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.stream.IntStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * Consumer-side idempotency, tested without a broker.
 *
 * <p>Kafka is not involved here on purpose. The property being tested — "applying the same
 * event twice has the same effect as applying it once" — is a property of the processor, not
 * of the transport. Testing it directly makes it fast and deterministic; the broker wiring is
 * proved separately in {@code KafkaIntegrationTest}.
 */
@SpringBootTest
class ConsumerIdempotencyTest {

    private static final String PAYLOAD = """
            {"transferId":"t-1","partnerId":"acme","amountMinorUnits":25000,
             "currency":"GBP","status":"RECEIVED"}""";

    @Autowired TransferEventProcessor processor;
    @Autowired ProcessedEventRepository processedEvents;
    @Autowired PartnerVolumeRepository volumes;

    @BeforeEach
    void clean() {
        processedEvents.deleteAll();
        volumes.deleteAll();
    }

    @Test
    @DisplayName("the same event delivered twice is applied once")
    void duplicateDeliveryIsIgnored() {
        assertThat(processor.process("evt-1", PAYLOAD)).isTrue();
        assertThat(processor.process("evt-1", PAYLOAD)).isFalse();

        // The counter is what makes the duplicate visible. Without the dedup row this would
        // be 50000 — a read model that is quietly, permanently wrong.
        var volume = volumes.findById("acme").orElseThrow();
        assertThat(volume.getEventCount()).isEqualTo(1);
        assertThat(volume.getTotalMinorUnits()).isEqualTo(25_000);
    }

    @Test
    @DisplayName("distinct events all apply")
    void distinctEventsAccumulate() {
        assertThat(processor.process("evt-1", PAYLOAD)).isTrue();
        assertThat(processor.process("evt-2", PAYLOAD)).isTrue();
        assertThat(processor.process("evt-3", PAYLOAD)).isTrue();

        var volume = volumes.findById("acme").orElseThrow();
        assertThat(volume.getEventCount()).isEqualTo(3);
        assertThat(volume.getTotalMinorUnits()).isEqualTo(75_000);
    }

    @Test
    @DisplayName("two consumer instances racing on one event apply it once")
    void concurrentConsumersApplyOnce() throws Exception {
        int threads = 8;
        CyclicBarrier startLine = new CyclicBarrier(threads);

        try (ExecutorService pool = Executors.newFixedThreadPool(threads)) {
            List<Callable<Boolean>> tasks = IntStream.range(0, threads)
                    .<Callable<Boolean>>mapToObj(i -> () -> {
                        startLine.await(10, TimeUnit.SECONDS);
                        return processor.process("evt-race", PAYLOAD);
                    })
                    .toList();

            long applied = pool.invokeAll(tasks).stream()
                    .map(f -> {
                        try {
                            return f.get(10, TimeUnit.SECONDS);
                        } catch (Exception e) {
                            // A losing thread may surface the constraint violation as a
                            // rollback exception rather than a clean false; either way it
                            // must not have applied the event.
                            return false;
                        }
                    })
                    .filter(Boolean::booleanValue)
                    .count();

            assertThat(applied).as("exactly one delivery applies").isEqualTo(1);

            var volume = volumes.findById("acme").orElseThrow();
            assertThat(volume.getEventCount()).isEqualTo(1);
            assertThat(volume.getTotalMinorUnits()).isEqualTo(25_000);
        }
    }

    @Test
    @DisplayName("an unparseable payload is a dead-letter case, not a retry case")
    void poisonMessageIsRejectedNotRetried() {
        assertThatThrownBy(() -> processor.process("evt-bad", "this is not json"))
                .isInstanceOf(TransferEventProcessor.UnprocessableEventException.class);

        // Retrying this forever would block every event behind it on the same partition.
        // It needs a dead-letter topic and an alert, not patience.
        assertThat(volumes.findById("acme")).isEmpty();
    }
}
