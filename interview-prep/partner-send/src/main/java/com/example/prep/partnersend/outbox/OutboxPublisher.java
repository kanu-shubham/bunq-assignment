package com.example.prep.partnersend.outbox;

import java.time.Clock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Drains the outbox table onto the broker.
 *
 * <p><b>The delivery guarantee is at-least-once, on purpose.</b> Publishing to the broker
 * and marking the row published are, once again, two systems — so the poller can succeed at
 * the first and die before the second, and the event goes out twice. The alternative
 * ordering (mark first, then publish) loses events instead, which is strictly worse: a
 * duplicate is a de-duplication problem, a lost payment event is an incident.
 *
 * <p>So: duplicates are expected, and every consumer must be idempotent. {@code eventId}
 * is the handle for that — a consumer keeps the ids it has processed and drops repeats.
 * "Exactly-once delivery" is not available over a network; exactly-once <em>effect</em> is,
 * and that is what at-least-once plus consumer idempotency buys.
 *
 * <p>One event failing must not block the ones behind it, so each is published in its own
 * try/catch. A permanently poisonous event would otherwise stall the whole queue — in
 * production, a row whose {@code attempts} exceeds a threshold gets moved aside to a
 * dead-letter table and alerted on rather than retried forever.
 */
@Component
public class OutboxPublisher {

    private static final Logger log = LoggerFactory.getLogger(OutboxPublisher.class);

    private final OutboxRepository repository;
    private final EventPublisher publisher;
    private final Clock clock;
    private final int batchSize;

    public OutboxPublisher(
            OutboxRepository repository,
            EventPublisher publisher,
            Clock clock,
            @org.springframework.beans.factory.annotation.Value("${outbox.batch-size:100}") int batchSize) {
        this.repository = repository;
        this.publisher = publisher;
        this.clock = clock;
        this.batchSize = batchSize;
    }

    /**
     * Polling is the boring, dependable option. The grown-up alternative is change data
     * capture — Debezium tailing the database's write-ahead log — which removes the polling
     * latency and the load, at the cost of running Kafka Connect. Polling every 500ms is
     * usually fine; reach for CDC when the poll interval becomes the dominant latency or
     * the table gets hot.
     */
    @Scheduled(fixedDelayString = "${outbox.poll-interval-ms:500}")
    public void pollAndPublish() {
        int published = drainOnce();
        if (published > 0) {
            log.debug("Published {} outbox event(s)", published);
        }
    }

    /** @return how many events were successfully published */
    @Transactional
    public int drainOnce() {
        var batch = repository.findUnpublished(PageRequest.ofSize(batchSize));
        int published = 0;
        for (OutboxEvent event : batch) {
            event.recordAttempt();
            try {
                publisher.publish(event);
                event.markPublished(clock.instant());
                published++;
            } catch (RuntimeException e) {
                // Leave publishedAt null so the next poll retries this row, and keep going
                // so one bad event does not hold up the queue behind it.
                log.warn("Failed to publish outbox event {} (attempt {}): {}",
                        event.getEventId(), event.getAttempts(), e.toString());
            }
        }
        repository.saveAll(batch);
        return published;
    }
}
