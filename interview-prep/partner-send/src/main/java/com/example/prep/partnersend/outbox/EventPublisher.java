package com.example.prep.partnersend.outbox;

/**
 * Where outbox rows go once they leave the database — Kafka, SNS, a partner webhook.
 *
 * <p>Kept as a one-method interface so the resilience and delivery semantics can be tested
 * without a broker. This is the same dependency-injection seam as the {@code submitFeedback}
 * prop in the bunq widget: the contract is narrow, production supplies the real thing, tests
 * supply one that fails on demand.
 */
@FunctionalInterface
public interface EventPublisher {

    /**
     * @throws RuntimeException if delivery failed; the event stays unpublished and is retried
     */
    void publish(OutboxEvent event);
}
