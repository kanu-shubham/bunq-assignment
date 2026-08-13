package com.example.prep.partnersend.api;

import com.example.prep.partnersend.domain.Money;
import com.example.prep.partnersend.domain.Transfer;
import com.example.prep.partnersend.idempotency.IdempotencyService;
import com.example.prep.partnersend.service.TransferService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.Valid;
import java.util.Currency;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * The partner-facing API.
 *
 * <p>The interesting method is {@link #createTransfer}, which is the idempotency protocol
 * expressed as HTTP status codes. The mapping is worth memorising, because "what do you
 * return when the same key arrives twice?" is a stock question and most answers are vague:
 *
 * <table border="1">
 *   <caption>Idempotency outcomes</caption>
 *   <tr><th>Situation</th><th>Status</th><th>Why</th></tr>
 *   <tr><td>First time</td><td>202 Accepted</td>
 *       <td>Recorded, not settled. Do not claim more than you know.</td></tr>
 *   <tr><td>Replay, same body</td><td>202 + identical body</td>
 *       <td>The point of idempotency: byte-identical answer, no new side effect.</td></tr>
 *   <tr><td>Same key, different body</td><td>422 Unprocessable</td>
 *       <td>A client bug. Silently replaying the first response would tell them a payment
 *           they never made had succeeded.</td></tr>
 *   <tr><td>Duplicate in flight</td><td>409 Conflict + Retry-After</td>
 *       <td>We cannot answer yet without guessing. Say so and let them retry.</td></tr>
 * </table>
 */
@RestController
@RequestMapping("/v1/partners/{partnerId}/transfers")
public class TransferController {

    private final TransferService transferService;
    private final IdempotencyService idempotency;
    private final ObjectMapper objectMapper;

    public TransferController(
            TransferService transferService, IdempotencyService idempotency, ObjectMapper objectMapper) {
        this.transferService = transferService;
        this.idempotency = idempotency;
        this.objectMapper = objectMapper;
    }

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> createTransfer(
            @PathVariable String partnerId,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @Valid @RequestBody CreateTransferRequest request)
            throws JsonProcessingException {

        // Canonicalise before hashing: re-serialising the parsed object means whitespace and
        // key order in the raw body cannot make two identical requests look different.
        String canonical = objectMapper.writeValueAsString(request);

        var claim = idempotency.claim(partnerId, idempotencyKey, canonical);

        if (claim instanceof IdempotencyService.Claim.Replay replay) {
            return ResponseEntity.status(replay.status())
                    .header("Idempotent-Replay", "true")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(replay.body());
        }
        if (claim instanceof IdempotencyService.Claim.Conflict conflict) {
            return ResponseEntity.unprocessableEntity()
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(objectMapper.writeValueAsString(
                            new ApiError("idempotency_key_reuse", conflict.message())));
        }
        if (claim instanceof IdempotencyService.Claim.InFlight inFlight) {
            return ResponseEntity.status(HttpStatus.CONFLICT)
                    .header("Retry-After", "1")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(objectMapper.writeValueAsString(
                            new ApiError("request_in_flight", inFlight.message())));
        }

        String claimId = ((IdempotencyService.Claim.Acquired) claim).id();
        try {
            Money amount = new Money(
                    Long.parseLong(request.amountMinorUnits()), Currency.getInstance(request.currency()));
            Transfer transfer =
                    transferService.acceptTransfer(partnerId, request.partnerReference(), amount);

            String body = objectMapper.writeValueAsString(TransferResponse.from(transfer));
            // Only record the response after the work has committed. Completing the claim
            // first would make a rolled-back transfer permanently replayable as a success.
            idempotency.complete(claimId, HttpStatus.ACCEPTED.value(), body);

            return ResponseEntity.accepted().contentType(MediaType.APPLICATION_JSON).body(body);
        } catch (RuntimeException e) {
            // Free the key so the client's retry is not locked out until the reaper runs.
            // Safe because the transfer transaction rolled back: no money moved.
            idempotency.release(claimId);
            throw e;
        }
    }

    @GetMapping(value = "/{transferId}", produces = MediaType.APPLICATION_JSON_VALUE)
    public TransferResponse getTransfer(@PathVariable String partnerId, @PathVariable String transferId) {
        return TransferResponse.from(transferService.requireTransfer(transferId));
    }

    public record ApiError(String code, String message) {
    }
}
