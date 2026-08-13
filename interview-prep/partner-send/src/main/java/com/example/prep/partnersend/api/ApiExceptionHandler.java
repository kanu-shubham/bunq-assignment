package com.example.prep.partnersend.api;

import com.example.prep.partnersend.domain.IllegalTransitionException;
import com.example.prep.partnersend.partner.PartnerBankException;
import com.example.prep.partnersend.service.TransferService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Maps domain failures to status codes in one place.
 *
 * <p>The distinction that matters to a client is <b>"may I retry this?"</b>, and the status
 * code is how you tell them. A retryable partner failure must be a 503 with
 * {@code Retry-After}, never a generic 500 — a well-behaved client backs off on 503 and
 * gives up on 500, and getting this wrong either loses payments or invites a retry storm.
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(TransferService.TransferNotFoundException.class)
    public ResponseEntity<TransferController.ApiError> notFound(TransferService.TransferNotFoundException e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(new TransferController.ApiError("transfer_not_found", e.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<TransferController.ApiError> invalid(MethodArgumentNotValidException e) {
        String detail = e.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + ": " + f.getDefaultMessage())
                .reduce((a, b) -> a + "; " + b)
                .orElse("invalid request");
        return ResponseEntity.badRequest()
                .body(new TransferController.ApiError("invalid_request", detail));
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    public ResponseEntity<TransferController.ApiError> missingHeader(MissingRequestHeaderException e) {
        return ResponseEntity.badRequest()
                .body(new TransferController.ApiError("missing_header", e.getHeaderName() + " is required"));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<TransferController.ApiError> illegalArgument(IllegalArgumentException e) {
        return ResponseEntity.badRequest()
                .body(new TransferController.ApiError("invalid_request", e.getMessage()));
    }

    @ExceptionHandler(IllegalTransitionException.class)
    public ResponseEntity<TransferController.ApiError> illegalTransition(IllegalTransitionException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(new TransferController.ApiError("illegal_state_transition", e.getMessage()));
    }

    @ExceptionHandler(PartnerBankException.class)
    public ResponseEntity<TransferController.ApiError> partnerFailure(PartnerBankException e) {
        if (e.isRetryable()) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .header("Retry-After", "5")
                    .body(new TransferController.ApiError("partner_unavailable", e.getMessage()));
        }
        return ResponseEntity.unprocessableEntity()
                .body(new TransferController.ApiError("partner_rejected", e.getMessage()));
    }
}
