package com.paykit.common.error;

import com.paykit.common.api.ErrorCode;

/**
 * Base class for every business failure the platform raises deliberately.
 *
 * <p>JAVA CONCEPT — checked vs unchecked. This extends {@link RuntimeException} (unchecked)
 * on purpose: a declined card is not something a controller can meaningfully {@code catch}
 * and repair, so forcing {@code throws} clauses through every layer would add noise without
 * adding safety. One {@code @RestControllerAdvice} handles the whole family at the edge.
 *
 * <p>It also matters for transactions: Spring rolls back on unchecked exceptions by default,
 * which is the behaviour we want when a payment fails mid-write.
 */
public abstract class PlatformException extends RuntimeException {

    private final ErrorCode code;
    private final String param;

    protected PlatformException(ErrorCode code, String message) {
        this(code, message, null, null);
    }

    protected PlatformException(ErrorCode code, String message, String param) {
        this(code, message, param, null);
    }

    protected PlatformException(ErrorCode code, String message, String param, Throwable cause) {
        super(message, cause);
        this.code = code;
        this.param = param;
    }

    public ErrorCode code() {
        return code;
    }

    public String param() {
        return param;
    }
}
