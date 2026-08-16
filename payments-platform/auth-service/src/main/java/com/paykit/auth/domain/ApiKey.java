package com.paykit.auth.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

import java.time.Instant;
import java.util.Set;

/**
 * A secret API key, stored the way secrets must be stored: hashed, never in plaintext.
 *
 * <h3>The lookup problem</h3>
 * A password is checked against a known user row. An API key arrives with no username — the
 * key <em>is</em> the identity. But you cannot look up a bcrypt hash, because bcrypt salts
 * every hash differently, so the same key hashes to a different value each time.
 *
 * <p>The fix is a two-part key: {@code sk_test_ABCD1234_<secret>}. The prefix is stored in
 * plaintext and indexed, so it selects exactly one row; the secret half is bcrypt-verified
 * against that row. Constant work, no plaintext secret at rest.
 *
 * <p>WHY BCRYPT AND NOT SHA-256 — bcrypt is deliberately slow (~100ms here), which makes
 * brute-forcing a leaked database impractical. The flip side is that you cannot afford it on
 * every request, which is precisely why the gateway caches verification results in Redis.
 * Security and performance are being traded against each other in the open.
 */
@Entity
@Table(name = "api_keys")
@jakarta.persistence.EntityListeners(AuditingEntityListener.class)
public class ApiKey {

    @Id
    @Column(length = 64)
    private String id;

    /**
     * JPA CONCEPT — {@code FetchType.LAZY}. With the default EAGER, loading any key would
     * also load its merchant, and loading a list of keys would fire one extra query per row:
     * the N+1 problem. Lazy means the merchant is fetched only if something asks for it.
     */
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "merchant_id", nullable = false)
    private Merchant merchant;

    /** Public, indexed half of the key, e.g. {@code sk_test_ABCD1234}. Safe to log. */
    @Column(name = "key_prefix", nullable = false, unique = true, length = 32)
    private String keyPrefix;

    /** BCrypt hash of the secret half. Never logged, never returned by any endpoint. */
    @Column(name = "secret_hash", nullable = false)
    private String secretHash;

    @Column(nullable = false)
    private boolean livemode;

    @Column(nullable = false)
    private boolean revoked;

    @Column(name = "scopes", nullable = false, length = 512)
    private String scopes;

    @Column(name = "last_used_at")
    private Instant lastUsedAt;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    protected ApiKey() {
    }

    public ApiKey(String id, Merchant merchant, String keyPrefix, String secretHash,
                  boolean livemode, Set<String> scopes) {
        this.id = id;
        this.merchant = merchant;
        this.keyPrefix = keyPrefix;
        this.secretHash = secretHash;
        this.livemode = livemode;
        this.revoked = false;
        this.scopes = String.join(",", scopes);
    }

    public void revoke() {
        this.revoked = true;
    }

    public void markUsed() {
        this.lastUsedAt = Instant.now();
    }

    public boolean isUsable() {
        return !revoked && merchant.canAcceptPayments();
    }

    public Set<String> scopeSet() {
        return Set.of(scopes.split(","));
    }

    public String getId() {
        return id;
    }

    public Merchant getMerchant() {
        return merchant;
    }

    public String getKeyPrefix() {
        return keyPrefix;
    }

    public String getSecretHash() {
        return secretHash;
    }

    public boolean isLivemode() {
        return livemode;
    }

    public boolean isRevoked() {
        return revoked;
    }

    public Instant getLastUsedAt() {
        return lastUsedAt;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
