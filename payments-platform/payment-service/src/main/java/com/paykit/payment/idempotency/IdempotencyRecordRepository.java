package com.paykit.payment.idempotency;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;

public interface IdempotencyRecordRepository extends JpaRepository<IdempotencyRecord, String> {

    /**
     * Idempotency keys are only useful for as long as a client might retry — 24 hours is the
     * industry norm. Without this the table grows forever, and an unbounded table on the hot
     * path of every write is a slow-motion outage.
     */
    @Modifying
    @Transactional
    @Query("DELETE FROM IdempotencyRecord r WHERE r.createdAt < :threshold")
    int deleteOlderThan(Instant threshold);
}
