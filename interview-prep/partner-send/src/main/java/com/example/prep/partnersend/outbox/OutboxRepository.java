package com.example.prep.partnersend.outbox;

import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface OutboxRepository extends JpaRepository<OutboxEvent, Long> {

    /**
     * The next batch of events waiting to go out, oldest first.
     *
     * <p>Ordering by {@code sequenceNo} preserves per-aggregate ordering, because events for
     * a given transfer are inserted in causal order. Note that this gives ordering
     * <em>within</em> a transfer, not globally — which is the right trade. Global ordering
     * across all transfers would mean a single writer and no horizontal scaling, to buy a
     * guarantee nothing in the domain actually needs.
     *
     * <p><b>Running more than one poller.</b> This demo runs a single publisher, so a plain
     * SELECT is enough. In production you would add {@code FOR UPDATE SKIP LOCKED}:
     *
     * <pre>
     *   SELECT * FROM outbox_event WHERE published_at IS NULL
     *   ORDER BY sequence_no LIMIT 100 FOR UPDATE SKIP LOCKED
     * </pre>
     *
     * <p>Each replica then locks the rows it claimed and other replicas step over them
     * instead of blocking, so N pollers do N times the work. Without SKIP LOCKED they
     * queue behind one another and you have bought horizontal scaling you do not get.
     * It is omitted here because H2's support for the hint differs from Postgres's and
     * a demo should not have flaky tests.
     */
    @Query("select e from OutboxEvent e where e.publishedAt is null order by e.sequenceNo asc")
    List<OutboxEvent> findUnpublished(Pageable pageable);

    @Query("select e from OutboxEvent e where e.publishedAt is null order by e.sequenceNo asc")
    List<OutboxEvent> findUnpublished();

    List<OutboxEvent> findByAggregateIdOrderBySequenceNoAsc(String aggregateId);
}
