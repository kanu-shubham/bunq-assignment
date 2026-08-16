package com.paykit.webhook.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

import java.time.Instant;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * A merchant's registered URL, and the events they want sent to it.
 */
@Entity
@Table(name = "webhook_endpoints", indexes = {
        @Index(name = "idx_endpoint_merchant", columnList = "merchant_id")
})
public class WebhookEndpoint {

    public enum Status {
        ENABLED,
        /** Turned off after too many consecutive failures, so a dead URL stops costing us. */
        DISABLED
    }

    @Id
    @Column(length = 64)
    private String id;

    @Column(name = "merchant_id", nullable = false, length = 64)
    private String merchantId;

    @Column(nullable = false, length = 500)
    private String url;

    /**
     * The HMAC signing secret, shared with the merchant once at creation.
     *
     * <p>Unlike an API key this is stored in plaintext, because signing requires the original
     * value — a hash cannot produce an HMAC. In production this column belongs in a KMS or
     * behind Postgres column encryption; storing it as plain text here is a simplification and
     * is called out rather than hidden.
     */
    @Column(nullable = false, length = 128)
    private String secret;

    /** Comma-separated event types, or {@code *} for everything. */
    @Column(name = "enabled_events", nullable = false, length = 1000)
    private String enabledEvents;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private Status status;

    @Column(name = "consecutive_failures", nullable = false)
    private int consecutiveFailures;

    @Column(nullable = false, length = 200)
    private String description;

    @Version
    private Long version;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected WebhookEndpoint() {
    }

    public WebhookEndpoint(String id, String merchantId, String url, String secret,
                           Set<String> enabledEvents, String description) {
        this.id = id;
        this.merchantId = merchantId;
        this.url = url;
        this.secret = secret;
        this.enabledEvents = enabledEvents == null || enabledEvents.isEmpty()
                ? "*" : String.join(",", enabledEvents);
        this.status = Status.ENABLED;
        this.consecutiveFailures = 0;
        this.description = description == null ? "" : description;
        this.createdAt = Instant.now();
    }

    public boolean wants(String eventType) {
        if (status != Status.ENABLED) {
            return false;
        }
        Set<String> events = eventSet();
        return events.contains("*") || events.contains(eventType);
    }

    public Set<String> eventSet() {
        return new LinkedHashSet<>(Arrays.asList(enabledEvents.split(",")));
    }

    public void recordSuccess() {
        this.consecutiveFailures = 0;
    }

    /**
     * Auto-disables after a long run of failures.
     *
     * <p>Endpoints do get abandoned — a merchant decommissions a server and forgets the
     * webhook. Retrying a URL that has failed 50 times in a row wastes our capacity and, if
     * the host still resolves, looks like an attack from their side.
     */
    public void recordFailure(int disableThreshold) {
        this.consecutiveFailures++;
        if (consecutiveFailures >= disableThreshold) {
            this.status = Status.DISABLED;
        }
    }

    public void enable() {
        this.status = Status.ENABLED;
        this.consecutiveFailures = 0;
    }

    public boolean belongsTo(String candidateMerchantId) {
        return merchantId.equals(candidateMerchantId);
    }

    public String getId() {
        return id;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public String getUrl() {
        return url;
    }

    public String getSecret() {
        return secret;
    }

    public Status getStatus() {
        return status;
    }

    public int getConsecutiveFailures() {
        return consecutiveFailures;
    }

    public String getDescription() {
        return description;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Long getVersion() {
        return version;
    }
}
