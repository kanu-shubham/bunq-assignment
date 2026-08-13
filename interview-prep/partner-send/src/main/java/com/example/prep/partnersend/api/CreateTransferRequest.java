package com.example.prep.partnersend.api;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

/**
 * Inbound payload. Note that the amount arrives as a <b>string of minor units</b>, not a
 * JSON number.
 *
 * <p>JSON numbers are IEEE-754 doubles in most parsers, so {@code 10.10} can arrive as
 * {@code 10.099999999999999}. Taking the amount as a digit string and parsing it ourselves
 * means no float ever touches the value. Stripe, Adyen and GoCardless all do this, and for
 * this reason.
 */
public record CreateTransferRequest(
        @NotBlank(message = "partnerReference is required")
        String partnerReference,

        @NotBlank(message = "amountMinorUnits is required")
        @Pattern(regexp = "\\d{1,18}", message = "amountMinorUnits must be a positive integer string")
        String amountMinorUnits,

        @NotBlank(message = "currency is required")
        @Pattern(regexp = "[A-Z]{3}", message = "currency must be a 3-letter ISO code")
        String currency,

        @NotBlank(message = "beneficiaryIban is required")
        String beneficiaryIban) {
}
