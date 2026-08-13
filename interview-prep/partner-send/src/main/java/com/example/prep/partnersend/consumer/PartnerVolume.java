package com.example.prep.partnersend.consumer;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

/**
 * A read model built by consuming the event stream: how much each partner has sent.
 *
 * <p>It exists to make duplicate processing <em>visible</em>. A counter is the clearest
 * possible demonstration of why consumer idempotency matters — if the same event is applied
 * twice, the total is simply wrong, and no amount of staring at the transfer table shows it.
 *
 * <p>This is also what people mean by CQRS in its useful, un-grand form: the write side owns
 * transfers, the read side owns aggregates like this one, and events carry state between
 * them. The read model is eventually consistent, and for a volume dashboard that is entirely
 * fine.
 */
@Entity
@Table(name = "partner_volume")
public class PartnerVolume {

    @Id
    @Column(name = "partner_id", nullable = false, updatable = false, length = 64)
    private String partnerId;

    @Column(name = "event_count", nullable = false)
    private long eventCount;

    @Column(name = "total_minor_units", nullable = false)
    private long totalMinorUnits;

    @Version
    @Column(name = "version", nullable = false)
    private long version;

    protected PartnerVolume() {
    }

    public PartnerVolume(String partnerId) {
        this.partnerId = partnerId;
        this.eventCount = 0;
        this.totalMinorUnits = 0;
    }

    public void add(long minorUnits) {
        this.eventCount++;
        this.totalMinorUnits += minorUnits;
    }

    public String getPartnerId() {
        return partnerId;
    }

    public long getEventCount() {
        return eventCount;
    }

    public long getTotalMinorUnits() {
        return totalMinorUnits;
    }
}
