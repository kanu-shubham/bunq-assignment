package com.paykit.payment.outbox;

import com.paykit.common.kafka.Topics;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Drains the outbox into Kafka.
 *
 * <h3>Why polling, and why that is fine</h3>
 * The alternative is change-data-capture (Debezium reading the write-ahead log), which is
 * lower-latency and considerably more moving parts. A 500ms poll adds at most half a second to
 * event delivery — irrelevant for a ledger posting or a webhook — and needs no extra
 * infrastructure. Choose CDC when the latency budget or the write volume actually demands it.
 *
 * <h3>Running on every instance at once</h3>
 * There is no leader election here. {@code claimUnpublished} uses
 * {@code FOR UPDATE SKIP LOCKED}, so each instance grabs a disjoint batch and they scale
 * horizontally by accident rather than by design. See {@link OutboxEventRepository}.
 *
 * <h3>Ordering</h3>
 * The Kafka key is the aggregate id, so every event for one payment lands on one partition and
 * arrives in order. Events for <em>different</em> payments have no ordering guarantee between
 * them — and correctly so, since they are independent.
 */
@Component
public class OutboxPublisher {

    private static final Logger log = LoggerFactory.getLogger(OutboxPublisher.class);

    private final OutboxEventRepository repository;
    private final KafkaTemplate<String, String> kafkaTemplate;
    private final Counter publishedCounter;
    private final Counter failedCounter;
    private final int batchSize;

    public OutboxPublisher(OutboxEventRepository repository,
                           KafkaTemplate<String, String> kafkaTemplate,
                           MeterRegistry meterRegistry,
                           @Value("${paykit.outbox.batch-size:100}") int batchSize) {
        this.repository = repository;
        this.kafkaTemplate = kafkaTemplate;
        this.batchSize = batchSize;
        this.publishedCounter = Counter.builder("paykit.outbox.published")
                .description("Domain events successfully published to Kafka")
                .register(meterRegistry);
        this.failedCounter = Counter.builder("paykit.outbox.failed")
                .description("Outbox publish attempts that failed")
                .register(meterRegistry);
    }

    /**
     * {@code fixedDelay} (not {@code fixedRate}) measures the gap <em>after</em> the previous
     * run finishes. With fixedRate, a run slower than the interval would have the next one
     * starting immediately, stacking up under exactly the load where that hurts most.
     */
    @Scheduled(fixedDelayString = "${paykit.outbox.poll-interval-ms:500}")
    @Transactional
    public void publishPending() {
        List<OutboxEvent> batch = repository.claimUnpublished(batchSize);
        if (batch.isEmpty()) {
            return;
        }

        log.debug("Publishing {} outbox events", batch.size());

        for (OutboxEvent event : batch) {
            try {
                // Blocking on the send is intentional: the row must not be marked published
                // until the broker has actually acknowledged it. Fire-and-forget here would
                // reintroduce exactly the lost-event problem the outbox exists to prevent.
                kafkaTemplate.send(Topics.PAYMENT_EVENTS, event.getAggregateId(), event.getPayload())
                        .get(10, TimeUnit.SECONDS);

                event.markPublished();
                publishedCounter.increment();
            } catch (InterruptedException ex) {
                // Restore the flag: swallowing an interrupt hides a shutdown request from
                // everything further up the stack.
                Thread.currentThread().interrupt();
                event.markFailed("Interrupted during publish");
                failedCounter.increment();
                return;
            } catch (Exception ex) {
                // Leave published_at NULL so the next poll retries it. The row stays claimed
                // only until this transaction ends.
                log.warn("Failed to publish outbox event {} (attempt {}): {}",
                        event.getEventId(), event.getAttempts() + 1, ex.getMessage());
                event.markFailed(ex.getMessage());
                failedCounter.increment();
            }
        }
    }

    /** Keeps the table from growing without bound; published rows are audit history at best. */
    @Scheduled(cron = "${paykit.outbox.cleanup-cron:0 0 3 * * *}")
    @Transactional
    public void purgePublished() {
        int deleted = repository.deletePublishedOlderThan(
                java.time.Instant.now().minus(Duration.ofDays(7)));
        if (deleted > 0) {
            log.info("Purged {} published outbox events", deleted);
        }
    }

    /**
     * An event that has failed many times will not fix itself. Surfacing it as a log line an
     * alert can match on is the difference between noticing in minutes and noticing when a
     * merchant complains that their webhooks stopped.
     */
    @Scheduled(fixedDelayString = "${paykit.outbox.poison-check-ms:60000}")
    @Transactional(readOnly = true)
    public void reportPoisonedEvents() {
        List<OutboxEvent> poisoned = repository.findPoisoned(5);
        if (!poisoned.isEmpty()) {
            log.error("{} outbox events have failed 5+ times and need attention: {}",
                    poisoned.size(), poisoned.stream().map(OutboxEvent::getEventId).toList());
        }
    }
}
