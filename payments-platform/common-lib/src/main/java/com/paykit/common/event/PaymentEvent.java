package com.paykit.common.event;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

import java.time.Instant;

/**
 * Every domain fact the payment service publishes to Kafka.
 *
 * <p>JAVA CONCEPT — {@code sealed interface} (Java 17+). "Sealed" means the set of
 * implementations is fixed <em>at compile time</em>: only the types listed in
 * {@code permits} may implement it. That buys two things:
 *
 * <ol>
 *   <li><b>Exhaustive switches.</b> A {@code switch} over a sealed type needs no
 *       {@code default} branch, so when a new event is added the compiler points at every
 *       consumer that has not handled it — instead of that fact surfacing as a production
 *       incident at 3am. See {@code LedgerPostingService} for a switch that relies on this.</li>
 *   <li><b>A readable domain.</b> The interface is a table of contents for the system.</li>
 * </ol>
 *
 * <p>The Jackson annotations write a {@code "type"} discriminator into the JSON so the same
 * closed hierarchy survives the trip through Kafka.
 */
// visible = false (the default): the "type" property is consumed by the type resolver and is
// NOT also handed to the record constructor, which has no matching component. Setting it true
// here is a classic Jackson trap that fails only on the read path.
@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, include = JsonTypeInfo.As.PROPERTY, property = "type")
@JsonSubTypes({
        @JsonSubTypes.Type(value = PaymentEvents.PaymentIntentCreated.class, name = PaymentEvent.PAYMENT_INTENT_CREATED),
        @JsonSubTypes.Type(value = PaymentEvents.PaymentSucceeded.class, name = PaymentEvent.PAYMENT_SUCCEEDED),
        @JsonSubTypes.Type(value = PaymentEvents.PaymentFailed.class, name = PaymentEvent.PAYMENT_FAILED),
        @JsonSubTypes.Type(value = PaymentEvents.PaymentCanceled.class, name = PaymentEvent.PAYMENT_CANCELED),
        @JsonSubTypes.Type(value = PaymentEvents.RefundSucceeded.class, name = PaymentEvent.REFUND_SUCCEEDED)
})
public sealed interface PaymentEvent permits
        PaymentEvents.PaymentIntentCreated,
        PaymentEvents.PaymentSucceeded,
        PaymentEvents.PaymentFailed,
        PaymentEvents.PaymentCanceled,
        PaymentEvents.RefundSucceeded {

    String PAYMENT_INTENT_CREATED = "payment_intent.created";
    String PAYMENT_SUCCEEDED = "payment_intent.succeeded";
    String PAYMENT_FAILED = "payment_intent.payment_failed";
    String PAYMENT_CANCELED = "payment_intent.canceled";
    String REFUND_SUCCEEDED = "refund.succeeded";

    /** Globally unique event id — the consumer's deduplication key. */
    String eventId();

    /** Wire discriminator, e.g. {@code payment_intent.succeeded}. */
    String type();

    /** Tenant the event belongs to. Consumers must never leak across merchants. */
    String merchantId();

    /**
     * The aggregate this event is about. Used as the Kafka partition key so that all
     * events for one payment land on one partition and are therefore consumed in order.
     */
    String aggregateId();

    Instant occurredAt();
}
