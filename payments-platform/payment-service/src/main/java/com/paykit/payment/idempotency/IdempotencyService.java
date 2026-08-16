package com.paykit.payment.idempotency;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.paykit.common.error.Exceptions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Optional;

/**
 * Reserve → execute → complete, with two layers of protection.
 *
 * <h3>Layer 1: Redis (fast, advisory)</h3>
 * {@code SET key value NX EX 60} is an atomic "claim this if nobody has". It rejects a
 * concurrent duplicate in under a millisecond without touching Postgres. It is <em>advisory</em>:
 * if Redis is unavailable or has been flushed, correctness must not depend on it.
 *
 * <h3>Layer 2: the unique constraint (slow, authoritative)</h3>
 * Two requests that both get past Redis still race to INSERT the same primary key. Exactly one
 * INSERT succeeds; the other gets a constraint violation, which this class translates into
 * "already in progress". <b>The database is the guarantee.</b> Redis is an optimisation.
 *
 * <p>Whenever you see a distributed lock, ask what happens when the lock service fails. If the
 * answer is "we double-charge", the lock is not a correctness mechanism and something else
 * must be.
 */
@Service
public class IdempotencyService {

    private static final Logger log = LoggerFactory.getLogger(IdempotencyService.class);
    private static final String LOCK_PREFIX = "idem:lock:";
    private static final Duration LOCK_TTL = Duration.ofSeconds(60);

    private final IdempotencyRecordRepository repository;
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;

    public IdempotencyService(IdempotencyRecordRepository repository,
                              StringRedisTemplate redis,
                              ObjectMapper objectMapper) {
        this.repository = repository;
        this.redis = redis;
        this.objectMapper = objectMapper;
    }

    /**
     * Fingerprints the request body. Two requests with the same key must carry the same body,
     * and comparing hashes avoids storing the body itself — which would mean keeping payment
     * details around for 24 hours for no good reason.
     */
    public String hash(Object requestBody) {
        try {
            byte[] canonical = objectMapper.writeValueAsBytes(requestBody);
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(canonical));
        } catch (Exception ex) {
            throw new IllegalStateException("Unable to fingerprint request", ex);
        }
    }

    @Transactional(readOnly = true)
    public Optional<IdempotencyRecord> find(String merchantId, String key) {
        return repository.findById(IdempotencyRecord.scopedKey(merchantId, key));
    }

    /**
     * Claims the key.
     *
     * <p>{@code Propagation.REQUIRES_NEW} suspends any surrounding transaction and commits this
     * INSERT on its own. That is essential: the reservation must be durable <em>before</em> the
     * card is charged, so a crash mid-charge still leaves evidence that the request was seen.
     * Joining the caller's transaction would roll the reservation back with everything else.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void reserve(String merchantId, String key, String requestHash, String endpoint) {
        String lockKey = LOCK_PREFIX + IdempotencyRecord.scopedKey(merchantId, key);

        Boolean acquired = tryAcquire(lockKey);
        if (Boolean.FALSE.equals(acquired)) {
            throw new Exceptions.ConcurrentModificationException("Request with idempotency key", key);
        }

        try {
            repository.saveAndFlush(new IdempotencyRecord(merchantId, key, requestHash, endpoint));
        } catch (DataIntegrityViolationException ex) {
            // Redis said yes but the database said no — either Redis was flushed, or the TTL
            // expired under a very slow request. The constraint is the source of truth.
            log.debug("Idempotency key {} already reserved in the database", key);
            throw new Exceptions.ConcurrentModificationException("Request with idempotency key", key);
        }
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void complete(String merchantId, String key, int status, Object responseBody, String resourceId) {
        repository.findById(IdempotencyRecord.scopedKey(merchantId, key)).ifPresent(record -> {
            try {
                record.complete(status, objectMapper.writeValueAsString(responseBody), resourceId);
            } catch (Exception ex) {
                log.warn("Could not store idempotent response for key {}: {}", key, ex.getMessage());
            }
        });
        releaseLock(merchantId, key);
    }

    /**
     * Called when the operation failed. Dropping the reservation lets the client retry the
     * same key — which is exactly what they should do after a 503, and what they could not do
     * if a failed attempt permanently burned the key.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void release(String merchantId, String key) {
        repository.deleteById(IdempotencyRecord.scopedKey(merchantId, key));
        releaseLock(merchantId, key);
    }

    /** Replays a stored response into the controller method's declared return type. */
    public <T> T replay(IdempotencyRecord record, Class<T> type) {
        try {
            return objectMapper.readValue(record.getResponseBody(), type);
        } catch (Exception ex) {
            throw new IllegalStateException(
                    "Stored idempotent response for %s is unreadable".formatted(record.getScopedKey()), ex);
        }
    }

    /** Scheduled cleanup; see {@code IdempotencyCleanupJob}. */
    @Transactional
    public int purgeOlderThan(Duration retention) {
        return repository.deleteOlderThan(Instant.now().minus(retention));
    }

    private Boolean tryAcquire(String lockKey) {
        try {
            return redis.opsForValue().setIfAbsent(lockKey, "1", LOCK_TTL);
        } catch (Exception ex) {
            // Redis is down. Degrade to the database constraint rather than failing the
            // request: availability is preserved and correctness never depended on Redis.
            log.warn("Idempotency lock unavailable, relying on the database constraint: {}", ex.getMessage());
            return null;
        }
    }

    private void releaseLock(String merchantId, String key) {
        try {
            redis.delete(LOCK_PREFIX + IdempotencyRecord.scopedKey(merchantId, key));
        } catch (Exception ex) {
            log.debug("Could not release idempotency lock (it will expire): {}", ex.getMessage());
        }
    }
}
