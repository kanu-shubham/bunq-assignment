package com.paykit.payment.web;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import com.paykit.payment.domain.Charge;
import com.paykit.payment.domain.PaymentIntent;
import com.paykit.payment.domain.Refund;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * The public API shapes.
 *
 * <p>These are versioned by the URL ({@code /v1/...}) and are the contract merchants build
 * against. Adding a field is safe; renaming or removing one is a breaking change that needs a
 * new version, which is why they are kept separate from the entities that back them — the
 * database schema needs to be free to change without breaking anybody's integration.
 */
public final class PaymentDtos {

    private PaymentDtos() {
        throw new AssertionError("No instances");
    }

    // ---------- requests ----------

    public record CreatePaymentIntentRequest(
            /* Minor units. 1000 = 10.00 EUR. Documented loudly because it is the single most
               common integration mistake, and getting it wrong is a factor-of-100 error. */
            @NotNull @Min(1) @Max(99_999_999L) Long amount,
            @NotNull Currency currency,
            @Size(max = 64) String customerId,
            @Size(max = 500) String description,
            Map<String, String> metadata) {

        public Money toMoney() {
            return Money.of(amount, currency);
        }
    }

    public record ConfirmPaymentIntentRequest(@NotBlank @Size(max = 64) String paymentMethodId) {
    }

    public record CancelPaymentIntentRequest(@Size(max = 200) String reason) {
    }

    public record CreateRefundRequest(
            @NotBlank String charge,
            /* Optional: omit to refund the full remaining amount. */
            @Min(1) Long amount,
            Refund.Reason reason) {
    }

    // ---------- responses ----------

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record PaymentIntentResponse(
            String id,
            String object,
            long amount,
            Currency currency,
            String status,
            String customerId,
            String paymentMethodId,
            String description,
            Map<String, String> metadata,
            String latestChargeId,
            String failureCode,
            String failureMessage,
            int attemptCount,
            Instant createdAt,
            Instant succeededAt,
            Instant canceledAt) {

        public static PaymentIntentResponse from(PaymentIntent intent) {
            return new PaymentIntentResponse(
                    intent.getId(),
                    "payment_intent",
                    intent.getAmount().minorUnits(),
                    intent.getAmount().currency(),
                    intent.getStatus().wireValue(),
                    intent.getCustomerId(),
                    intent.getPaymentMethodId(),
                    intent.getDescription(),
                    intent.getMetadata(),
                    intent.getChargeId(),
                    intent.getFailureCode(),
                    intent.getFailureMessage(),
                    intent.getAttemptCount(),
                    intent.getCreatedAt(),
                    intent.getSucceededAt(),
                    intent.getCanceledAt());
        }
    }

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record ChargeResponse(
            String id,
            String object,
            String paymentIntentId,
            long amount,
            long fee,
            long net,
            long amountRefunded,
            Currency currency,
            String cardBrand,
            String cardLast4,
            boolean refunded,
            Instant createdAt) {

        public static ChargeResponse from(Charge charge) {
            return new ChargeResponse(
                    charge.getId(),
                    "charge",
                    charge.getPaymentIntentId(),
                    charge.getAmount().minorUnits(),
                    charge.getFee().minorUnits(),
                    charge.getNet().minorUnits(),
                    charge.getRefunded().minorUnits(),
                    charge.getCurrency(),
                    charge.getCardBrand(),
                    charge.getCardLast4(),
                    charge.isFullyRefunded(),
                    charge.getCreatedAt());
        }
    }

    /** Returned by confirm: the settled intent plus the charge it produced. */
    public record ConfirmationResponse(PaymentIntentResponse paymentIntent, ChargeResponse charge) {
    }

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record RefundResponse(
            String id,
            String object,
            String chargeId,
            String paymentIntentId,
            long amount,
            long feeRefunded,
            String reason,
            String status,
            Instant createdAt) {

        public static RefundResponse from(Refund refund) {
            return new RefundResponse(
                    refund.getId(),
                    "refund",
                    refund.getChargeId(),
                    refund.getPaymentIntentId(),
                    refund.getAmount().minorUnits(),
                    refund.getFeeRefunded().minorUnits(),
                    refund.getReason().name().toLowerCase(),
                    refund.getStatus().name().toLowerCase(),
                    refund.getCreatedAt());
        }
    }

    public record ListResponse<T>(String object, List<T> data, boolean hasMore, long totalCount) {

        public static <T> ListResponse<T> of(List<T> data, boolean hasMore, long totalCount) {
            return new ListResponse<>("list", data, hasMore, totalCount);
        }
    }
}
