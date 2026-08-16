package com.paykit.payment.acquirer;

import com.paykit.common.money.Currency;

/**
 * The contract with the card network. Deliberately narrow: the less the payment domain knows
 * about the acquirer's wire format, the easier it is to add a second acquirer later.
 */
public final class AcquirerModels {

    private AcquirerModels() {
        throw new AssertionError("No instances");
    }

    public record AuthorizationRequest(
            String paymentIntentId,
            String merchantId,
            long amountMinor,
            Currency currency,
            String paymentMethodId,
            String idempotencyKey) {
    }

    /**
     * Note that a <em>decline</em> is a successful call with a negative answer, not an error.
     * Modelling it as an exception would make the circuit breaker treat a customer's expired
     * card as evidence that the acquirer is unhealthy — and after enough declines it would trip
     * the breaker and stop legitimate traffic. Transport failures are exceptional; business
     * outcomes are return values.
     */
    public record AuthorizationResponse(
            boolean approved,
            String acquirerReference,
            String declineCode,
            String declineMessage,
            String cardBrand,
            String cardLast4,
            String network) {

        public static AuthorizationResponse approved(String reference, String brand, String last4, String network) {
            return new AuthorizationResponse(true, reference, null, null, brand, last4, network);
        }

        public static AuthorizationResponse declined(String code, String message, String brand, String last4) {
            return new AuthorizationResponse(false, null, code, message, brand, last4, null);
        }
    }

    public record RefundRequest(
            String chargeId,
            String acquirerReference,
            long amountMinor,
            Currency currency,
            String reason) {
    }

    public record RefundResponse(boolean approved, String acquirerReference, String failureCode) {
    }
}
