package com.paykit.common.web;

import com.paykit.common.api.ApiError;
import com.paykit.common.api.ErrorCode;
import com.paykit.common.error.PlatformException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.NoHandlerFoundException;

import java.util.List;

/**
 * Turns exceptions into the platform's single error envelope.
 *
 * <p>SPRING CONCEPT — {@code @RestControllerAdvice} is an AOP-style interceptor around every
 * controller. Handling errors here rather than in each method keeps controllers free of
 * try/catch and guarantees that a client never sees two different error shapes.
 *
 * <p>SECURITY — unexpected exceptions are logged with their stack trace but the response
 * says only "internal error" plus the correlation id. Leaking a stack trace, SQL statement
 * or class name to the internet is how attackers map your system.
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    /** One handler for the entire {@link PlatformException} family — polymorphism doing the work. */
    @ExceptionHandler(PlatformException.class)
    public ResponseEntity<ApiError> handlePlatform(PlatformException ex) {
        ErrorCode code = ex.code();
        if (code.status().is5xxServerError()) {
            log.error("Platform error [{}]: {}", code.wireValue(), ex.getMessage(), ex);
        } else {
            log.warn("Platform error [{}]: {}", code.wireValue(), ex.getMessage());
        }
        return ResponseEntity.status(code.status())
                .body(ApiError.of(code, ex.getMessage(), ex.param(), RequestContext.correlationId()));
    }

    /** Bean-validation failures (@Valid on a @RequestBody) become a field-by-field 400. */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleValidation(MethodArgumentNotValidException ex) {
        List<ApiError.FieldViolation> violations = ex.getBindingResult().getFieldErrors().stream()
                .map(fe -> new ApiError.FieldViolation(fe.getField(), fe.getDefaultMessage()))
                .toList();
        return ResponseEntity.badRequest()
                .body(ApiError.validation("Request validation failed", RequestContext.correlationId(), violations));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> handleUnreadable(HttpMessageNotReadableException ex) {
        return ResponseEntity.badRequest().body(ApiError.of(
                ErrorCode.INVALID_REQUEST, "Malformed JSON request body", RequestContext.correlationId()));
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    public ResponseEntity<ApiError> handleMissingHeader(MissingRequestHeaderException ex) {
        return ResponseEntity.badRequest().body(ApiError.of(
                ErrorCode.INVALID_REQUEST,
                "Missing required header: " + ex.getHeaderName(),
                RequestContext.correlationId()));
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ApiError> handleTypeMismatch(MethodArgumentTypeMismatchException ex) {
        return ResponseEntity.badRequest().body(ApiError.of(
                ErrorCode.INVALID_REQUEST,
                "Invalid value for parameter '%s'".formatted(ex.getName()),
                ex.getName(),
                RequestContext.correlationId()));
    }

    @ExceptionHandler(NoHandlerFoundException.class)
    public ResponseEntity<ApiError> handleNoHandler(NoHandlerFoundException ex) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ApiError.of(
                ErrorCode.RESOURCE_NOT_FOUND,
                "Unrecognized request URL: %s %s".formatted(ex.getHttpMethod(), ex.getRequestURL()),
                RequestContext.correlationId()));
    }

    /**
     * Two transactions updated the same row; JPA's @Version caught it. This is a normal,
     * retryable outcome under load, not a bug — so it is a 409, not a 500.
     */
    @ExceptionHandler(OptimisticLockingFailureException.class)
    public ResponseEntity<ApiError> handleOptimisticLock(OptimisticLockingFailureException ex) {
        log.warn("Optimistic lock conflict: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.CONFLICT).body(ApiError.of(
                ErrorCode.RESOURCE_CONFLICT,
                "The resource was modified concurrently. Please retry the request.",
                RequestContext.correlationId()));
    }

    /** The catch-all. Never let an unexpected exception reach the client unshaped. */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> handleUnexpected(Exception ex) {
        log.error("Unhandled exception", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(ApiError.of(
                ErrorCode.INTERNAL_ERROR,
                "An unexpected error occurred. Quote the request id when contacting support.",
                RequestContext.correlationId()));
    }
}
