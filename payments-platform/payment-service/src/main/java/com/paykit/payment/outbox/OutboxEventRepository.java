package com.paykit.payment.outbox;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;

public interface OutboxEventRepository extends JpaRepository<OutboxEvent, Long> {

    /**
     * The publisher's claim query, and the most interesting SQL in the codebase.
     *
     * <pre>
     *   FOR UPDATE SKIP LOCKED
     * </pre>
     *
     * <p>Two payment-service instances both poll this table every second. Plain
     * {@code FOR UPDATE} would make instance B <em>wait</em> for instance A's batch — correct,
     * but the poller is now effectively single-threaded and B burns a connection doing nothing.
     * {@code SKIP LOCKED} tells Postgres "give me unlocked rows and ignore the rest", so B
     * picks up the next batch immediately and the two instances share the work with no
     * coordinator, no leader election and no distributed lock.
     *
     * <p>This is a native query because JPQL has no way to express SKIP LOCKED.
     */
    @Query(value = """
            SELECT * FROM outbox_events
            WHERE published_at IS NULL
            ORDER BY id
            LIMIT :batchSize
            FOR UPDATE SKIP LOCKED
            """, nativeQuery = true)
    List<OutboxEvent> claimUnpublished(@Param("batchSize") int batchSize);

    long countByPublishedAtIsNull();

    /** Housekeeping: published rows are history, not state. */
    @Modifying
    @Query("DELETE FROM OutboxEvent e WHERE e.publishedAt IS NOT NULL AND e.publishedAt < :threshold")
    int deletePublishedOlderThan(Instant threshold);

    /** Alerting hook: rows that keep failing need a human, not another retry. */
    @Query("SELECT e FROM OutboxEvent e WHERE e.publishedAt IS NULL AND e.attempts >= :minAttempts")
    List<OutboxEvent> findPoisoned(int minAttempts);
}
