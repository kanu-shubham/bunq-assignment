package com.paykit.payment.outbox;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.paykit.common.event.EventEnvelope;
import com.paykit.common.event.PaymentEvent;
import com.paykit.common.web.RequestContext;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Writes an event into the outbox table.
 *
 * <p>{@code Propagation.MANDATORY} is the important annotation here: it throws if there is no
 * transaction already in progress. That turns "someone recorded an event outside the business
 * transaction" from a subtle correctness bug — an event that survives a rolled-back payment —
 * into an immediate, obvious failure. The type system cannot express "must be called inside a
 * transaction", so this is the next best thing.
 */
@Component
public class OutboxRecorder {

    private final OutboxEventRepository repository;
    private final ObjectMapper objectMapper;

    public OutboxRecorder(OutboxEventRepository repository, ObjectMapper objectMapper) {
        this.repository = repository;
        this.objectMapper = objectMapper;
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void record(PaymentEvent event) {
        String correlationId = RequestContext.correlationId();
        EventEnvelope<PaymentEvent> envelope = EventEnvelope.wrap(event, correlationId);

        String payload;
        try {
            payload = objectMapper.writeValueAsString(envelope);
        } catch (Exception ex) {
            // Failing the whole transaction is correct: a payment we cannot announce is worse
            // than a payment that did not happen. Silence here means a permanently wrong ledger.
            throw new IllegalStateException("Unable to serialise event " + event.type(), ex);
        }

        repository.save(new OutboxEvent(
                event.eventId(),
                event.aggregateId(),
                event.merchantId(),
                event.type(),
                payload,
                correlationId));
    }
}
