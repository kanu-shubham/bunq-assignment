package com.paykit.payment.web;

import com.paykit.common.error.Exceptions;
import com.paykit.payment.domain.PaymentIntent;
import com.paykit.payment.domain.PaymentIntentStatus;
import com.paykit.payment.idempotency.Idempotent;
import com.paykit.payment.service.PaymentIntentService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.data.domain.Page;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * The payments API.
 *
 * <p>{@code X-Merchant-Id} is injected by the gateway after it authenticates the caller and is
 * never accepted from the client — see {@code AuthenticationFilter}. Every method passes it into
 * the service, and every repository query filters on it, so tenant isolation is enforced at the
 * data layer rather than remembered at the controller layer.
 *
 * <p>The methods return DTOs rather than {@code ResponseEntity} wherever the status is fixed;
 * that keeps them readable and lets {@code @Idempotent} replay a stored response by simply
 * deserialising into the declared return type.
 */
@RestController
@RequestMapping("/v1/payment_intents")
@Tag(name = "Payment Intents", description = "Create, confirm and cancel payments")
public class PaymentIntentController {

    private final PaymentIntentService paymentIntentService;

    public PaymentIntentController(PaymentIntentService paymentIntentService) {
        this.paymentIntentService = paymentIntentService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    @Idempotent("create_payment_intent")
    @Operation(summary = "Create a payment intent",
            description = "Send an Idempotency-Key header so that a retry after a network "
                    + "failure returns the original payment instead of creating a second one.")
    public PaymentDtos.PaymentIntentResponse create(
            @RequestHeader("X-Merchant-Id") String merchantId,
            @Valid @RequestBody PaymentDtos.CreatePaymentIntentRequest request) {

        PaymentIntent intent = paymentIntentService.create(
                merchantId,
                request.toMoney(),
                request.customerId(),
                request.description(),
                request.metadata());

        return PaymentDtos.PaymentIntentResponse.from(intent);
    }

    /**
     * Charges the card.
     *
     * <p>An Idempotency-Key is <b>required</b> here, not merely recommended: this is the call
     * that moves money, and a client that retries it without one has no way to know whether
     * the first attempt succeeded.
     */
    @PostMapping("/{paymentIntentId}/confirm")
    @Idempotent(value = "confirm_payment_intent", required = true)
    @Operation(summary = "Confirm a payment intent and charge the payment method")
    public PaymentDtos.ConfirmationResponse confirm(
            @RequestHeader("X-Merchant-Id") String merchantId,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey,
            @PathVariable String paymentIntentId,
            @Valid @RequestBody PaymentDtos.ConfirmPaymentIntentRequest request) {

        PaymentIntentService.ConfirmationResult result = paymentIntentService.confirm(
                merchantId, paymentIntentId, request.paymentMethodId(), idempotencyKey);

        return new PaymentDtos.ConfirmationResponse(
                PaymentDtos.PaymentIntentResponse.from(result.intent()),
                PaymentDtos.ChargeResponse.from(result.charge()));
    }

    @PostMapping("/{paymentIntentId}/cancel")
    @Operation(summary = "Cancel a payment intent that has not been captured")
    public PaymentDtos.PaymentIntentResponse cancel(
            @RequestHeader("X-Merchant-Id") String merchantId,
            @PathVariable String paymentIntentId,
            @RequestBody(required = false) PaymentDtos.CancelPaymentIntentRequest request) {

        return PaymentDtos.PaymentIntentResponse.from(paymentIntentService.cancel(
                merchantId, paymentIntentId, request == null ? null : request.reason()));
    }

    @GetMapping("/{paymentIntentId}")
    public PaymentDtos.PaymentIntentResponse get(
            @RequestHeader("X-Merchant-Id") String merchantId,
            @PathVariable String paymentIntentId) {

        return PaymentDtos.PaymentIntentResponse.from(
                paymentIntentService.require(merchantId, paymentIntentId));
    }

    @GetMapping
    @Operation(summary = "List payment intents, newest first")
    public PaymentDtos.ListResponse<PaymentDtos.PaymentIntentResponse> list(
            @RequestHeader("X-Merchant-Id") String merchantId,
            @Parameter(description = "Filter by status, e.g. succeeded")
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int limit) {

        Page<PaymentIntent> result = paymentIntentService.list(
                merchantId, parseStatus(status), page, limit);

        return PaymentDtos.ListResponse.of(
                result.map(PaymentDtos.PaymentIntentResponse::from).getContent(),
                result.hasNext(),
                result.getTotalElements());
    }

    /** Turns an unknown status into a 400 rather than an unhelpful 500 from valueOf(). */
    private static PaymentIntentStatus parseStatus(String status) {
        if (status == null || status.isBlank()) {
            return null;
        }
        try {
            return PaymentIntentStatus.valueOf(status.toUpperCase());
        } catch (IllegalArgumentException ex) {
            throw new Exceptions.InvalidRequestException(
                    "Unknown status '%s'".formatted(status), "status");
        }
    }
}
