package com.example.prep.partnersend.idempotency;

import java.time.Instant;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * Spring Data generates the implementation of this interface at startup. Method names
 * like {@code findById} come from {@link JpaRepository}; anything more specific is either
 * derived from the method name or, as below, spelled out in JPQL.
 */
public interface IdempotencyRepository extends JpaRepository<IdempotencyRecord, String> {

    /**
     * Deletes claims stranded IN_PROGRESS by a crash. The state is passed as a bound
     * parameter rather than written inline as a fully-qualified enum constant, which keeps
     * the query portable across Hibernate versions.
     */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query("delete from IdempotencyRecord r where r.state = :state and r.createdAt < :cutoff")
    int deleteClaimsInStateOlderThan(
            @Param("state") IdempotencyRecord.State state, @Param("cutoff") Instant cutoff);

    /** Convenience wrapper so callers do not have to name the state every time. */
    default int deleteStaleClaims(Instant cutoff) {
        return deleteClaimsInStateOlderThan(IdempotencyRecord.State.IN_PROGRESS, cutoff);
    }
}
