package com.paykit.ledger.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.paykit.common.event.EventEnvelope;
import com.paykit.common.event.PaymentEvent;
import com.paykit.common.kafka.Topics;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Reads payment events and hands them to the ledger.
 *
 * <h3>The erasure detail</h3>
 * <pre>
 *   objectMapper.readValue(json, EventEnvelope.class)                    // loses the payload type
 *   objectMapper.readValue(json, new TypeReference&lt;EventEnvelope&lt;PaymentEvent&gt;&gt;() {})  // works
 * </pre>
 * At runtime {@code EventEnvelope<PaymentEvent>} is just {@code EventEnvelope} — the type
 * argument is erased. {@code TypeReference} is an anonymous subclass, and the generic
 * information of a <em>supertype</em> survives in the class file, so Jackson can read it back.
 * That trick is the standard workaround for erasure across the whole Java ecosystem.
 *
 * <h3>Errors</h3>
 * This method deliberately does not catch anything. Throwing is what tells Spring Kafka not to
 * commit the offset, and lets the configured error handler retry and then dead-letter. A
 * try/catch that logs and swallows would silently drop a payment from the books.
 */
@Component
public class PaymentEventConsumer {

    private static final Logger log = LoggerFactory.getLogger(PaymentEventConsumer.class);
    private static final TypeReference<EventEnvelope<PaymentEvent>> ENVELOPE_TYPE =
            new TypeReference<>() {};

    private final ObjectMapper objectMapper;
    private final LedgerPostingService postingService;
    private final Counter processedCounter;
    private final Counter skippedCounter;

    public PaymentEventConsumer(ObjectMapper objectMapper,
                                LedgerPostingService postingService,
                                MeterRegistry meterRegistry) {
        this.objectMapper = objectMapper;
        this.postingService = postingService;
        this.processedCounter = meterRegistry.counter("paykit.ledger.events.processed");
        this.skippedCounter = meterRegistry.counter("paykit.ledger.events.skipped");
    }

    @KafkaListener(topics = Topics.PAYMENT_EVENTS, groupId = Topics.LEDGER_GROUP)
    public void onPaymentEvent(ConsumerRecord<String, String> record) throws Exception {
        EventEnvelope<PaymentEvent> envelope = objectMapper.readValue(record.value(), ENVELOPE_TYPE);

        // Carry the producer's correlation id into this service's logs, so one id follows the
        // request from the gateway all the way into the ledger.
        MDC.put("correlationId", envelope.correlationId() == null ? "-" : envelope.correlationId());
        try {
            log.debug("Consuming {} ({}) from partition {} offset {}",
                    envelope.type(), envelope.eventId(), record.partition(), record.offset());

            boolean posted = postingService.apply(envelope.data());
            if (posted) {
                processedCounter.increment();
            } else {
                skippedCounter.increment();
            }
        } finally {
            MDC.remove("correlationId");
        }
    }

    /**
     * Watches the dead-letter topic so a parked message is at least loud. In production this
     * feeds an alert; a DLQ nobody looks at is a silent data-loss queue.
     */
    @KafkaListener(topics = Topics.PAYMENT_EVENTS_DLQ, groupId = Topics.LEDGER_GROUP + "-dlq")
    public void onDeadLetter(ConsumerRecord<String, String> record) {
        log.error("Dead-lettered payment event at offset {}: {}", record.offset(), record.value());
    }
}
