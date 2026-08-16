package com.paykit.auth.service;

import com.paykit.auth.config.AuthProperties;
import com.paykit.auth.domain.ApiKey;
import io.jsonwebtoken.Jwts;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;
import java.util.List;

import static io.jsonwebtoken.security.Keys.hmacShaKeyFor;

/**
 * Trades a long-lived API key for a short-lived JWT.
 *
 * <h3>Why bother, when the API key already works?</h3>
 * Verifying an API key costs a database read plus ~100ms of bcrypt. Verifying a JWT costs an
 * HMAC over a few hundred bytes — no I/O at all — so the gateway can authenticate at line rate.
 * The client pays that cost once every 30 minutes instead of on every request.
 *
 * <p>THE TRADE-OFF — a JWT cannot be revoked, because nothing is consulted to validate it.
 * Revoke a key at 10:00 and a token minted at 09:59 keeps working until it expires. Short TTLs
 * bound that window; if you need instant revocation you need a token blacklist in Redis, and
 * you have re-introduced the lookup you were trying to avoid. There is no free lunch here,
 * only an explicit choice about how much staleness is acceptable.
 *
 * <p>{@code HS256} (a shared secret) is used because both services are ours. Were third
 * parties verifying these tokens, {@code RS256} would be correct: they get the public key and
 * can verify without being able to mint.
 */
@Service
public class TokenService {

    private final SecretKey signingKey;
    private final AuthProperties properties;

    public TokenService(AuthProperties properties) {
        this.properties = properties;
        this.signingKey = hmacShaKeyFor(properties.jwtSecret().getBytes(StandardCharsets.UTF_8));
    }

    public IssuedToken issue(ApiKey apiKey) {
        Instant now = Instant.now();
        Instant expiry = now.plus(properties.tokenTtl());

        String token = Jwts.builder()
                .issuer(properties.issuer())
                .subject(apiKey.getMerchant().getId())   // who the token speaks for
                .issuedAt(Date.from(now))
                .expiration(Date.from(expiry))
                .claim("kid", apiKey.getId())            // which key minted it, for audit + revocation
                .claim("livemode", apiKey.isLivemode())
                .claim("scopes", List.copyOf(apiKey.scopeSet()))
                .signWith(signingKey)
                .compact();

        return new IssuedToken(token, "Bearer", properties.tokenTtl().toSeconds(), expiry);
    }

    public record IssuedToken(String accessToken, String tokenType, long expiresIn, Instant expiresAt) {
    }
}
