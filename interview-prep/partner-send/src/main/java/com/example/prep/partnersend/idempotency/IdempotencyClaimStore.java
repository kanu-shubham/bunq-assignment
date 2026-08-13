package com.example.prep.partnersend.idempotency;

import java.time.Clock;
import java.time.Duration;
import java.util.Optional;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * The transactional half of the idempotency protocol, deliberately in its own bean.
 *
 * <p><b>Why not just put these methods on {@link IdempotencyService}?</b> Because Spring's
 * {@code @Transactional} is implemented with a proxy: callers get a wrapper object that
 * opens a transaction and then delegates to the real instance. A call from one method of
 * a class to another method of the <em>same</em> class goes straight to {@code this} and
 * never touches the proxy — so the annotation is silently ignored. The transaction you
 * carefully asked for does not exist, and nothing warns you.
 *
 * <p>That trap is worth knowing by name: <b>self-invocation defeats Spring AOP</b>. It
 * applies equally to {@code @Async}, {@code @Cacheable} and {@code @Retryable}. The fix is
 * the one used here — move the annotated method to a collaborator that is injected, so the
 * call crosses a proxy boundary for real.
 */
@Component
public class IdempotencyClaimStore {

    private final IdempotencyRepository repository;
    private final Clock clock;

    public IdempotencyClaimStore(IdempotencyRepository repository, Clock clock) {
        this.repository = repository;
        this.clock = clock;
    }

    /**
     * Inserts the claim in a transaction of its own that commits before the caller's work
     * begins, making the claim visible to concurrent duplicates immediately.
     *
     * <p>{@code saveAndFlush} rather than {@code save}: Hibernate otherwise defers the
     * INSERT until commit, and the primary-key violation we rely on would be thrown from
     * somewhere outside the try/catch that is meant to handle it.
     *
     * @throws org.springframework.dao.DataIntegrityViolationException if the key is taken
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public String insertClaim(String partnerId, String idempotencyKey, String requestHash) {
        IdempotencyRecord record =
                IdempotencyRecord.claim(partnerId, idempotencyKey, requestHash, clock.instant());
        repository.saveAndFlush(record);
        return record.getId();
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW, readOnly = true)
    public Optional<IdempotencyRecord> find(String partnerId, String idempotencyKey) {
        return repository.findById(IdempotencyRecord.key(partnerId, idempotencyKey));
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void complete(String claimId, int status, String body) {
        repository.findById(claimId).ifPresent(record -> {
            record.complete(status, body);
            repository.save(record);
        });
    }

    /**
     * Drops a claim whose work failed, so the client's retry is not locked out until the
     * reaper runs. Safe precisely because the work is transactional: if we are releasing,
     * the transfer transaction rolled back and no money moved.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void release(String claimId) {
        repository.deleteById(claimId);
    }

    /**
     * Frees keys stranded IN_PROGRESS by a crash between claim and complete.
     *
     * <p>{@code olderThan} must exceed the longest the protected work can possibly take,
     * including its own retries. Set it too short and the reaper re-opens a key while the
     * original request is still running — which is exactly the double-payment this whole
     * mechanism exists to prevent.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public int reclaimStale(Duration olderThan) {
        return repository.deleteStaleClaims(clock.instant().minus(olderThan));
    }
}
