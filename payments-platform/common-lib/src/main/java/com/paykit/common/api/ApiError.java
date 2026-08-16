package com.paykit.common.api;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.time.Instant;
import java.util.List;

/**
 * The single error envelope every service returns, so a client writes one error parser.
 *
 * <pre>
 * { "error": { "code": "card_declined", "message": "Your card was declined.",
 *              "param": "payment_method", "request_id": "req_...", "type": "card_error" } }
 * </pre>
 *
 * <p>{@code @JsonInclude(NON_NULL)} keeps optional fields out of the payload entirely
 * rather than emitting a wall of {@code null}s.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ApiError(Body error) {

    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Body(
            String code,
            String message,
            String param,
            String requestId,
            Instant timestamp,
            List<FieldViolation> violations) {
    }

    /** One failed bean-validation constraint, e.g. {@code amount must be greater than 0}. */
    public record FieldViolation(String field, String message) {
    }

    public static ApiError of(ErrorCode code, String message, String requestId) {
        return new ApiError(new Body(code.wireValue(), message, null, requestId, Instant.now(), null));
    }

    public static ApiError of(ErrorCode code, String message, String param, String requestId) {
        return new ApiError(new Body(code.wireValue(), message, param, requestId, Instant.now(), null));
    }

    public static ApiError validation(String message, String requestId, List<FieldViolation> violations) {
        return new ApiError(new Body(
                ErrorCode.INVALID_REQUEST.wireValue(), message, null, requestId, Instant.now(), violations));
    }
}
