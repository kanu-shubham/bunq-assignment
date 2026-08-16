package com.paykit.common.api;

import org.springframework.http.HttpStatus;

/**
 * The complete, closed set of machine-readable error codes the platform returns.
 *
 * <p>Clients switch on {@code code}; humans read {@code message}. Because each constant
 * carries its own HTTP status, the mapping lives in one place instead of being re-decided
 * in every controller.
 */
public enum ErrorCode {

    INVALID_REQUEST("invalid_request", HttpStatus.BAD_REQUEST),
    AUTHENTICATION_REQUIRED("authentication_required", HttpStatus.UNAUTHORIZED),
    PERMISSION_DENIED("permission_denied", HttpStatus.FORBIDDEN),
    RESOURCE_NOT_FOUND("resource_not_found", HttpStatus.NOT_FOUND),
    IDEMPOTENCY_CONFLICT("idempotency_conflict", HttpStatus.CONFLICT),
    RESOURCE_CONFLICT("resource_conflict", HttpStatus.CONFLICT),
    INVALID_STATE_TRANSITION("invalid_state_transition", HttpStatus.CONFLICT),
    CARD_DECLINED("card_declined", HttpStatus.PAYMENT_REQUIRED),
    RATE_LIMITED("rate_limited", HttpStatus.TOO_MANY_REQUESTS),
    ACQUIRER_UNAVAILABLE("acquirer_unavailable", HttpStatus.SERVICE_UNAVAILABLE),
    SERVICE_UNAVAILABLE("service_unavailable", HttpStatus.SERVICE_UNAVAILABLE),
    INTERNAL_ERROR("internal_error", HttpStatus.INTERNAL_SERVER_ERROR);

    private final String wireValue;
    private final HttpStatus status;

    ErrorCode(String wireValue, HttpStatus status) {
        this.wireValue = wireValue;
        this.status = status;
    }

    public String wireValue() {
        return wireValue;
    }

    public HttpStatus status() {
        return status;
    }
}
