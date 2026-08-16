package com.paykit.common.event;

import com.paykit.common.money.Money;

import java.time.Instant;
import java.util.Map;

/**
 * The concrete events. Each is a {@code record} implementing the sealed
 * {@link PaymentEvent} interface — the record's components satisfy the interface's
 * accessor methods automatically, so there is no boilerplate to write.
 *
 * <p>DESIGN — events are <em>facts about the past</em>, so they are immutable and named in
 * the past tense. They carry the data a consumer needs rather than forcing it to call back
 * into the payment service, which would re-couple the services we just decoupled.
 */
public final class PaymentEvents {

    private PaymentEvents() {
        throw new AssertionError("No instances");
    }

    public record PaymentIntentCreated(
            String eventId,
            String merchantId,
            String aggregateId,
            Money amount,
            String customerId,
            String description,
            Map<String, String> metadata,
            Instant occurredAt) implements PaymentEvent {

        @Override
        public String type() {
            return PAYMENT_INTENT_CREATED;
        }
    }

    public record PaymentSucceeded(
            String eventId,
            String merchantId,
            String aggregateId,
            String chargeId,
            Money amount,
            Money processingFee,
            String paymentMethodId,
            String cardBrand,
            String cardLast4,
            String acquirerReference,
            Instant occurredAt) implements PaymentEvent {

        @Override
        public String type() {
            return PAYMENT_SUCCEEDED;
        }

        /** Amount the merchant actually receives once the platform fee is taken. */
        public Money netAmount() {
            return amount.minus(processingFee);
        }
    }

    public record PaymentFailed(
            String eventId,
            String merchantId,
            String aggregateId,
            Money amount,
            String failureCode,
            String failureMessage,
            int attempt,
            Instant occurredAt) implements PaymentEvent {

        @Override
        public String type() {
            return PAYMENT_FAILED;
        }
    }

    public record PaymentCanceled(
            String eventId,
            String merchantId,
            String aggregateId,
            Money amount,
            String reason,
            Instant occurredAt) implements PaymentEvent {

        @Override
        public String type() {
            return PAYMENT_CANCELED;
        }
    }

    public record RefundSucceeded(
            String eventId,
            String merchantId,
            String aggregateId,
            String refundId,
            String chargeId,
            Money amount,
            Money feeRefunded,
            String reason,
            Instant occurredAt) implements PaymentEvent {

        @Override
        public String type() {
            return REFUND_SUCCEEDED;
        }
    }
}
