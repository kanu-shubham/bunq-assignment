package com.example.prep.partnersend.consumer;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Applies a transfer event to the read model, exactly once, however many times it is delivered.
 *
 * <h2>The single most important line in this class is {@code @Transactional}</h2>
 *
 * <p>Consumer idempotency only works if the de-duplication marker and the side effect commit
 * <b>together</b>. Consider the alternative:
 *
 * <pre>
 *   processedEvents.save(new ProcessedEvent(eventId));   // committed
 *   partnerVolume.add(amount);                           // ...crash here
 * </pre>
 *
 * <p>The event is now marked processed but was never applied, and because it is marked, the
 * redelivery is skipped. The update is lost forever. Reverse the order and a crash in the
 * middle applies the event twice instead.
 *
 * <p>That is the dual-write problem again — one level down, on the consumer side. Here it has
 * an easy answer, because both writes go to the same database: put them in one transaction.
 * Recognising that "consumer idempotency" and "the dedup marker must be transactional with the
 * effect" are the same requirement is the part that separates someone who has read about this
 * from someone who has built it.
 *
 * <p><b>Where the offset commit fits.</b> Spring commits the Kafka offset after this method
 * returns normally. If we crash before that, Kafka redelivers — and the dedup row (already
 * committed) makes the redelivery a no-op. If instead we crashed before the transaction
 * committed, nothing was applied and the redelivery does the work. Both orderings are safe,
 * which is the point of doing it this way.
 */
@Component
public class TransferEventProcessor {

    private static final Logger log = LoggerFactory.getLogger(TransferEventProcessor.class);

    private final ProcessedEventRepository processedEvents;
    private final PartnerVolumeRepository volumes;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    public TransferEventProcessor(
            ProcessedEventRepository processedEvents,
            PartnerVolumeRepository volumes,
            ObjectMapper objectMapper,
            Clock clock) {
        this.processedEvents = processedEvents;
        this.volumes = volumes;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    /**
     * @return true if this delivery was applied, false if it was a duplicate we skipped
     */
    @Transactional
    public boolean process(String eventId, String payload) {
        // Fast path. This check is an optimisation, not the guarantee — see below.
        if (processedEvents.existsById(eventId)) {
            log.debug("Skipping duplicate delivery of event {}", eventId);
            return false;
        }

        // Parse before writing anything, so a malformed payload fails without leaving a
        // half-applied transaction or a dedup row for an event that was never applied.
        JsonNode node;
        try {
            node = objectMapper.readTree(payload);
        } catch (com.fasterxml.jackson.core.JsonProcessingException e) {
            // A payload we cannot parse will never become parseable. Retrying forever would
            // block every event behind it on the partition, so this is a dead-letter case.
            throw new UnprocessableEventException(eventId, e);
        }

        String partnerId = node.get("partnerId").asText();
        long minorUnits = node.get("amountMinorUnits").asLong();

        // The dedup marker and the effect, in one transaction. Both or neither.
        processedEvents.save(new ProcessedEvent(eventId, clock.instant()));

        PartnerVolume volume = volumes.findById(partnerId)
                .orElseGet(() -> new PartnerVolume(partnerId));
        volume.add(minorUnits);
        volumes.save(volume);
        return true;
    }

    /*
     * ── Why the duplicate check is a plain existsById, and not a try/catch on the INSERT ──
     *
     * The idempotency layer claims its key by INSERTing and catching the constraint
     * violation. That works there because the claim lives in its own transaction. It does
     * NOT work here, and the difference is worth understanding.
     *
     * Once a constraint violation happens inside a transaction, Spring marks that
     * transaction rollback-only. Catching the exception does not un-mark it: the method
     * returns normally, and then the commit fails with UnexpectedRollbackException —
     * "Transaction silently rolled back because it has been marked as rollback-only".
     * (This code had exactly that bug, and ConsumerIdempotencyTest caught it.)
     *
     * We cannot escape it by claiming in a separate transaction either, because the whole
     * point here is that the dedup marker and the effect must commit together. So:
     *
     *   - existsById handles the overwhelmingly common case (a genuine redelivery),
     *     cheaply and with no exception.
     *   - The primary key still enforces correctness. If two consumers race past the
     *     check, one commits and the other's whole transaction rolls back — nothing is
     *     half-applied. Its Kafka offset is never committed, so the event is redelivered,
     *     and the retry takes the fast path and skips. At-least-once delivery is what makes
     *     losing that race harmless.
     *
     * On Postgres you would tighten this further with INSERT ... ON CONFLICT DO NOTHING,
     * which makes the claim atomic and race-free without ever raising an exception. It is
     * omitted here only because the syntax is dialect-specific.
     */

    /** Signals a permanently bad message — route to the dead-letter topic, do not retry. */
    public static class UnprocessableEventException extends RuntimeException {
        public UnprocessableEventException(String eventId, Throwable cause) {
            super("Event " + eventId + " cannot be parsed and will never succeed", cause);
        }
    }
}
