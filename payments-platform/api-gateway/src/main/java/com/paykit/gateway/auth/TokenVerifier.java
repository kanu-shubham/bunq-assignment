package com.paykit.gateway.auth;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.paykit.gateway.config.GatewayProperties;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static io.jsonwebtoken.security.Keys.hmacShaKeyFor;

/**
 * Verifies the two credential types the platform accepts, and caches the expensive one.
 *
 * <h3>1. JWT — {@code Authorization: Bearer eyJhbGci...}</h3>
 * Stateless. The gateway checks the HMAC signature and expiry with a shared secret and reads
 * the merchant straight out of the claims. <b>Zero network calls</b>, microseconds per request.
 * The cost is revocation: a JWT stays valid until it expires, which is why we issue short ones.
 *
 * <h3>2. API key — {@code Authorization: Bearer sk_test_...}</h3>
 * Stateful. The key must be looked up in auth-service, because only a database can say
 * "this key was revoked ten seconds ago". A network call per request would be unacceptable,
 * so the result is cached in Redis for a short TTL — long enough to remove almost all the
 * load, short enough that a revoked key stops working quickly. That trade-off (staleness vs
 * latency) is the whole of caching, in one decision.
 *
 * <p>SECURITY — the cache key is a SHA-256 of the credential, never the credential itself.
 * If someone dumps Redis, they get hashes, not usable keys.
 *
 * <p>REACTIVE — every method returns a {@link Mono}. Nothing here blocks; the event loop
 * moves on to other requests while Redis or auth-service is answering.
 */
@Component
public class TokenVerifier {

    private static final Logger log = LoggerFactory.getLogger(TokenVerifier.class);
    private static final String CACHE_PREFIX = "gw:apikey:";
    private static final String NEGATIVE_MARKER = "invalid";

    private final SecretKey jwtKey;
    private final GatewayProperties properties;
    private final ReactiveStringRedisTemplate redis;
    private final WebClient authClient;
    private final ObjectMapper objectMapper;

    public TokenVerifier(GatewayProperties properties,
                         ReactiveStringRedisTemplate redis,
                         WebClient.Builder loadBalancedWebClientBuilder,
                         ObjectMapper objectMapper) {
        this.properties = properties;
        this.redis = redis;
        this.objectMapper = objectMapper;
        this.jwtKey = hmacShaKeyFor(properties.jwtSecret().getBytes(StandardCharsets.UTF_8));
        // "lb://auth-service" — the load balancer resolves this to a live instance per call.
        this.authClient = loadBalancedWebClientBuilder.baseUrl("lb://auth-service").build();
    }

    public Mono<Principal> verify(String credential) {
        if (credential == null || credential.isBlank()) {
            return Mono.empty();
        }
        return credential.startsWith("sk_") || credential.startsWith("pk_")
                ? verifyApiKey(credential)
                : verifyJwt(credential);
    }

    /** Pure CPU work — parse, check signature, check expiry. Safe on an event-loop thread. */
    private Mono<Principal> verifyJwt(String token) {
        try {
            Claims claims = Jwts.parser()
                    .verifyWith(jwtKey)
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();

            @SuppressWarnings("unchecked")
            List<String> scopes = claims.get("scopes", List.class);

            return Mono.just(new Principal(
                    claims.getSubject(),
                    claims.get("kid", String.class),
                    Boolean.TRUE.equals(claims.get("livemode", Boolean.class)),
                    scopes == null ? Set.of() : Set.copyOf(scopes)));
        } catch (JwtException | IllegalArgumentException ex) {
            // Never log the token itself, and never tell the caller *why* it failed —
            // "expired" vs "bad signature" is free information for an attacker.
            log.debug("Rejected JWT: {}", ex.getMessage());
            return Mono.empty();
        }
    }

    private Mono<Principal> verifyApiKey(String apiKey) {
        String cacheKey = CACHE_PREFIX + sha256(apiKey);

        return redis.opsForValue().get(cacheKey)
                .flatMap(cached -> NEGATIVE_MARKER.equals(cached)
                        // Negative caching matters: without it, a bot spraying invalid keys
                        // sends every single request through to auth-service and its database.
                        ? Mono.<Principal>empty()
                        : Mono.justOrEmpty(deserialize(cached)))
                .switchIfEmpty(Mono.defer(() -> lookupAndCache(apiKey, cacheKey)))
                // Redis being down must degrade the gateway, not break it: fall through to
                // the authoritative source rather than rejecting every request.
                .onErrorResume(ex -> {
                    log.warn("API key cache unavailable, falling back to auth-service: {}", ex.getMessage());
                    return lookupAndCache(apiKey, cacheKey).onErrorResume(inner -> Mono.empty());
                });
    }

    private Mono<Principal> lookupAndCache(String apiKey, String cacheKey) {
        return authClient.post()
                .uri("/internal/api_keys/verify")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(Map.of("api_key", apiKey))
                .retrieve()
                .bodyToMono(VerifyResponse.class)
                .flatMap(response -> {
                    Principal principal = new Principal(
                            response.merchantId(), response.keyId(), response.livemode(),
                            response.scopes() == null ? Set.of() : Set.copyOf(response.scopes()));
                    return cache(cacheKey, serialize(principal)).thenReturn(principal);
                })
                .onErrorResume(ex -> {
                    log.debug("API key verification failed: {}", ex.toString());
                    return cache(cacheKey, NEGATIVE_MARKER).then(Mono.empty());
                });
    }

    private Mono<Boolean> cache(String key, String value) {
        return redis.opsForValue()
                .set(key, value, properties.apiKeyCacheTtl())
                .onErrorReturn(false);
    }

    private String serialize(Principal principal) {
        try {
            return objectMapper.writeValueAsString(principal);
        } catch (Exception ex) {
            throw new IllegalStateException("Unable to serialize principal", ex);
        }
    }

    private Principal deserialize(String json) {
        try {
            return objectMapper.readValue(json, Principal.class);
        } catch (Exception ex) {
            log.warn("Discarding unreadable cached principal");
            return null;
        }
    }

    private static String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception ex) {
            throw new IllegalStateException("SHA-256 unavailable", ex);
        }
    }

    /** The shape auth-service returns from {@code POST /internal/api_keys/verify}. */
    record VerifyResponse(String merchantId, String keyId, boolean livemode, List<String> scopes) {
    }
}
