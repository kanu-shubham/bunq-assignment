package com.paykit.gateway.config;

import com.paykit.gateway.filter.AuthenticationFilter;
import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import reactor.core.publisher.Mono;

import java.util.Optional;

/**
 * Distributed rate limiting, backed by Redis.
 *
 * <p>WHY REDIS AND NOT A LOCAL COUNTER — with three gateway instances behind nginx, a
 * per-instance counter of 100 rps actually allows 300 rps, and the real limit depends on
 * which instance nginx happened to pick. The bucket has to live somewhere all instances can
 * see, and Redis is that somewhere.
 *
 * <p>THE ALGORITHM — a <b>token bucket</b>, implemented inside Redis as a Lua script so that
 * "read the count, decide, write it back" happens atomically. Two parameters:
 * <ul>
 *   <li>{@code replenishRate} — sustained requests per second.</li>
 *   <li>{@code burstCapacity} — the size of the bucket, i.e. how big a spike is tolerated
 *       after a quiet period. Setting it above the replenish rate is what makes bursty but
 *       well-behaved clients work instead of being punished for batching.</li>
 * </ul>
 *
 * <p>The gateway responds 429 and sets {@code X-RateLimit-Remaining} so a good client can
 * back off before it gets there.
 */
@Configuration
public class RateLimiterConfig {

    @Bean
    @Primary
    public RedisRateLimiter redisRateLimiter(GatewayProperties properties) {
        return new RedisRateLimiter(
                properties.defaultRequestsPerSecond(),
                properties.defaultBurstCapacity(),
                1);
    }

    /**
     * What we count per. Per-merchant is the right unit: it is the tenant boundary, so one
     * customer's traffic spike cannot consume another's allowance.
     *
     * <p>Unauthenticated traffic has no merchant yet, so it falls back to the client IP —
     * which is why the gateway must read {@code X-Forwarded-For} and not the socket address,
     * since behind nginx every connection appears to come from nginx.
     */
    @Bean
    public KeyResolver merchantKeyResolver() {
        return exchange -> {
            String merchantId = exchange.getRequest().getHeaders()
                    .getFirst(AuthenticationFilter.MERCHANT_HEADER);
            if (merchantId != null && !merchantId.isBlank()) {
                return Mono.just("merchant:" + merchantId);
            }
            return Mono.just("ip:" + clientIp(exchange));
        };
    }

    private static String clientIp(org.springframework.web.server.ServerWebExchange exchange) {
        String forwarded = exchange.getRequest().getHeaders().getFirst("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            // X-Forwarded-For is a comma-separated chain; the original client is the first entry.
            return forwarded.split(",")[0].trim();
        }
        return Optional.ofNullable(exchange.getRequest().getRemoteAddress())
                .map(addr -> addr.getAddress().getHostAddress())
                .orElse("unknown");
    }
}
