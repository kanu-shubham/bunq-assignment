package com.example.prep.partnersend.idempotency;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.HexFormat;
import java.util.Optional;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;

/**
 * Implements the "claim, work, complete" idempotency protocol that every serious
 * payments API exposes to its clients.
 *
 * <pre>
 *   1. hash the request body
 *   2. INSERT (partnerId:key, hash, IN_PROGRESS)   ← separate transaction, commits at once
 *        success  → we own this key; caller does the work
 *        conflict → someone else owns it; go to 3
 *   3. SELECT the existing row
 *        COMPLETED + hash matches   → replay the stored response
 *        COMPLETED + hash differs   → key reused for a different body (422)
 *        IN_PROGRESS                → a duplicate is in flight right now (409, retry)
 *   4. once the work commits, UPDATE the row to COMPLETED with the response
 * </pre>
 *
 * <p><b>Why the database enforces this and not the code.</b> The tempting version is
 * "SELECT to see if the key exists, and INSERT if it doesn't". That is a check-then-act
 * race: two concurrent retries both read "absent", both proceed, and the customer is
 * paid twice. Here the uniqueness is a primary-key constraint, so the database — the one
 * component that can actually serialise the decision — picks the winner, and the loser
 * gets a constraint violation it can handle.
 *
 * <p><b>Why the request hash.</b> Without it, a client that recycles a key for a different
 * payment gets back the <em>first</em> payment's response and believes their second
 * payment succeeded. Comparing hashes turns that silent, expensive bug into a 422.
 */
@Service
public class IdempotencyService {

    private final IdempotencyClaimStore store;

    public IdempotencyService(IdempotencyClaimStore store) {
        this.store = store;
    }

    /** Outcome of trying to take ownership of an idempotency key. */
    public sealed interface Claim {
        /** We own the key. Do the work, then call {@link #complete} or {@link #release}. */
        record Acquired(String id) implements Claim {
        }

        /** A previous identical request already finished. Return this response verbatim. */
        record Replay(int status, String body) implements Claim {
        }

        /** Same key, different body. The client has a bug and needs to hear about it. */
        record Conflict(String message) implements Claim {
        }

        /** An identical request is in flight right now. Ask the client to retry. */
        record InFlight(String message) implements Claim {
        }
    }

    public Claim claim(String partnerId, String idempotencyKey, String requestBody) {
        String hash = sha256(requestBody);
        try {
            return new Claim.Acquired(store.insertClaim(partnerId, idempotencyKey, hash));
        } catch (DataIntegrityViolationException duplicate) {
            // Lost the race, or this is an honest client retry. Indistinguishable here,
            // and happily the handling is the same.
            return inspectExisting(partnerId, idempotencyKey, hash);
        }
    }

    private Claim inspectExisting(String partnerId, String idempotencyKey, String hash) {
        Optional<IdempotencyRecord> found = store.find(partnerId, idempotencyKey);
        if (found.isEmpty()) {
            // Rare: the winner rolled back and released between our INSERT failing and this
            // SELECT. Telling the client to retry is honest and safe.
            return new Claim.InFlight("Idempotency key is being processed; please retry");
        }
        IdempotencyRecord record = found.get();
        if (!record.matches(hash)) {
            return new Claim.Conflict(
                    "Idempotency key '%s' was already used with a different request body"
                            .formatted(idempotencyKey));
        }
        if (record.getState() == IdempotencyRecord.State.IN_PROGRESS) {
            return new Claim.InFlight("Idempotency key is being processed; please retry");
        }
        return new Claim.Replay(record.getResponseStatus(), record.getResponseBody());
    }

    public void complete(String claimId, int status, String body) {
        store.complete(claimId, status, body);
    }

    public void release(String claimId) {
        store.release(claimId);
    }

    public int reclaimStale(Duration olderThan) {
        return store.reclaimStale(olderThan);
    }

    static String sha256(String input) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(input.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required of every JVM", e);
        }
    }
}
