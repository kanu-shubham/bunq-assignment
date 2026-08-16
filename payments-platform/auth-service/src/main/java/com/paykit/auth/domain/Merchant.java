package com.paykit.auth.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

import java.time.Instant;

/**
 * A merchant account — the tenant boundary of the whole platform.
 *
 * <p>JPA CONCEPT — an {@code @Entity} is <em>mutable</em> and identity-based, the opposite of
 * the {@code record} value objects elsewhere in this codebase. Two {@code Merchant} instances
 * are the same merchant if their ids match, whatever their other fields say. That is why
 * entities must never be records: Hibernate needs a no-arg constructor and mutable fields to
 * hydrate them and to track changes.
 *
 * <p>The id is the public, prefixed {@code acct_...} string rather than a sequence. It is
 * assigned by the application, which means a merchant can be referenced across service
 * boundaries without a database round trip to translate ids.
 */
@Entity
@Table(name = "merchants")
@jakarta.persistence.EntityListeners(AuditingEntityListener.class)
public class Merchant {

    @Id
    @Column(length = 64)
    private String id;

    @Column(nullable = false)
    private String name;

    @Column(nullable = false, unique = true)
    private String email;

    @Column(name = "country_code", nullable = false, length = 2)
    private String countryCode;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private MerchantStatus status = MerchantStatus.PENDING;

    /**
     * JPA CONCEPT — {@code @Version} gives optimistic locking. Hibernate adds
     * {@code WHERE version = ?} to every UPDATE and bumps the column. If another transaction
     * got there first, zero rows match and Spring throws OptimisticLockingFailureException.
     * That turns a silent lost update into a loud, retryable 409.
     */
    @Version
    private Long version;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @LastModifiedDate
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    /** Required by JPA. Hibernate uses reflection to instantiate, then populates fields. */
    protected Merchant() {
    }

    public Merchant(String id, String name, String email, String countryCode) {
        this.id = id;
        this.name = name;
        this.email = email;
        this.countryCode = countryCode;
        this.status = MerchantStatus.PENDING;
    }

    /**
     * Behaviour lives on the entity, not in a service that reaches in and sets fields.
     * The entity is the one place that can guarantee its own invariants.
     */
    public void activate() {
        if (status == MerchantStatus.CLOSED) {
            throw new IllegalStateException("A closed merchant cannot be reactivated");
        }
        this.status = MerchantStatus.ACTIVE;
    }

    public void suspend(String ignoredReason) {
        this.status = MerchantStatus.SUSPENDED;
    }

    public boolean canAcceptPayments() {
        return status == MerchantStatus.ACTIVE;
    }

    public String getId() {
        return id;
    }

    public String getName() {
        return name;
    }

    public String getEmail() {
        return email;
    }

    public String getCountryCode() {
        return countryCode;
    }

    public MerchantStatus getStatus() {
        return status;
    }

    public Long getVersion() {
        return version;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
