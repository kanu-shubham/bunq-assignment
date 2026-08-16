package com.paykit.common.error;

import com.paykit.common.api.ErrorCode;

/**
 * The concrete failures, grouped in one file so the whole error vocabulary is readable
 * at a glance instead of spread over a dozen near-identical classes.
 */
public final class Exceptions {

    private Exceptions() {
        throw new AssertionError("No instances");
    }

    /** 404 — the resource does not exist, or does not belong to the calling merchant. */
    public static class NotFoundException extends PlatformException {
        public NotFoundException(String resource, String id) {
            super(ErrorCode.RESOURCE_NOT_FOUND, "No such %s: %s".formatted(resource, id));
        }
    }

    /** 400 — the request itself is malformed or violates a business rule. */
    public static class InvalidRequestException extends PlatformException {
        public InvalidRequestException(String message) {
            super(ErrorCode.INVALID_REQUEST, message);
        }

        public InvalidRequestException(String message, String param) {
            super(ErrorCode.INVALID_REQUEST, message, param);
        }
    }

    /**
     * 409 — the same Idempotency-Key was replayed with a <em>different</em> request body.
     * Replaying it with the same body is legal and returns the original response.
     */
    public static class IdempotencyConflictException extends PlatformException {
        public IdempotencyConflictException(String key) {
            super(ErrorCode.IDEMPOTENCY_CONFLICT,
                    ("Idempotency key '%s' was already used with a different request body. "
                            + "Reuse a key only when retrying the identical request.").formatted(key));
        }
    }

    /** 409 — e.g. confirming a payment intent that has already succeeded. */
    public static class InvalidStateTransitionException extends PlatformException {
        public InvalidStateTransitionException(String entity, Object from, Object to) {
            super(ErrorCode.INVALID_STATE_TRANSITION,
                    "%s cannot move from %s to %s".formatted(entity, from, to));
        }
    }

    /** 409 — optimistic locking lost a race; the caller may safely retry. */
    public static class ConcurrentModificationException extends PlatformException {
        public ConcurrentModificationException(String entity, String id) {
            super(ErrorCode.RESOURCE_CONFLICT,
                    "%s %s was modified concurrently, please retry".formatted(entity, id));
        }
    }

    /** 402 — the issuer said no. Carries the decline reason for the caller to display. */
    public static class CardDeclinedException extends PlatformException {
        private final String declineCode;

        public CardDeclinedException(String declineCode, String message) {
            super(ErrorCode.CARD_DECLINED, message, "payment_method");
            this.declineCode = declineCode;
        }

        public String declineCode() {
            return declineCode;
        }
    }

    /** 503 — the downstream card network is down or the circuit breaker is open. */
    public static class AcquirerUnavailableException extends PlatformException {
        public AcquirerUnavailableException(String message, Throwable cause) {
            super(ErrorCode.ACQUIRER_UNAVAILABLE, message, null, cause);
        }
    }

    /** 401 / 403. */
    public static class AuthenticationException extends PlatformException {
        public AuthenticationException(String message) {
            super(ErrorCode.AUTHENTICATION_REQUIRED, message);
        }
    }

    public static class PermissionDeniedException extends PlatformException {
        public PermissionDeniedException(String message) {
            super(ErrorCode.PERMISSION_DENIED, message);
        }
    }
}
