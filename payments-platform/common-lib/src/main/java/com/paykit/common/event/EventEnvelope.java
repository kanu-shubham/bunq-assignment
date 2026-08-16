package com.paykit.common.event;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.Instant;

/**
 * Transport metadata wrapped around a domain event.
 *
 * <p>Keeping envelope and payload separate means routing, tracing and schema evolution
 * concerns never leak into the domain event itself.
 *
 * <p>JAVA CONCEPT — <b>generics with a bounded type parameter</b>. {@code <T extends PaymentEvent>}
 * says "any payment event, but nothing else": {@code envelope.data()} is typed, no cast needed.
 *
 * <p>JAVA CONCEPT — <b>type erasure</b>. At runtime {@code EventEnvelope<PaymentSucceeded>} and
 * {@code EventEnvelope<PaymentFailed>} are the same class; the parameter is erased. That is why
 * a Kafka consumer cannot deserialize into {@code EventEnvelope<T>} from a {@code Class} literal
 * and must use Jackson's {@code TypeReference} instead — see {@code EventDeserializer}.
 */
public record EventEnvelope<T extends PaymentEvent>(
        String eventId,
        String type,
        String apiVersion,
        String correlationId,
        String merchantId,
        Instant createdAt,
        T data) {

    /** The API version this payload was serialised with. Bump it when the shape changes. */
    public static final String CURRENT_API_VERSION = "2026-01-01";

    @JsonCreator
    public EventEnvelope(
            @JsonProperty("eventId") String eventId,
            @JsonProperty("type") String type,
            @JsonProperty("apiVersion") String apiVersion,
            @JsonProperty("correlationId") String correlationId,
            @JsonProperty("merchantId") String merchantId,
            @JsonProperty("createdAt") Instant createdAt,
            @JsonProperty("data") T data) {
        this.eventId = eventId;
        this.type = type;
        this.apiVersion = apiVersion;
        this.correlationId = correlationId;
        this.merchantId = merchantId;
        this.createdAt = createdAt;
        this.data = data;
    }

    /**
     * Factory that derives the metadata from the event itself, so callers cannot
     * accidentally wrap a {@code PaymentSucceeded} in an envelope typed {@code payment_failed}.
     */
    public static <E extends PaymentEvent> EventEnvelope<E> wrap(E event, String correlationId) {
        return new EventEnvelope<>(
                event.eventId(),
                event.type(),
                CURRENT_API_VERSION,
                correlationId,
                event.merchantId(),
                Instant.now(),
                event);
    }
}
