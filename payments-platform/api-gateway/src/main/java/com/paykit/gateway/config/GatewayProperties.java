package com.paykit.gateway.config;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

import java.time.Duration;
import java.util.List;

/**
 * Type-safe configuration, bound from {@code paykit.gateway.*} in application.yml.
 *
 * <p>SPRING CONCEPT — {@code @ConfigurationProperties} beats scattering {@code @Value("${...}")}
 * around the codebase: the settings are grouped, documented, IDE-completable, and
 * {@code @Validated} makes a bad value fail at <em>startup</em> rather than on the first
 * request that happens to need it. A record makes the whole config object immutable.
 */
@Validated
@ConfigurationProperties(prefix = "paykit.gateway")
public record GatewayProperties(

        /* Paths served without authentication: health checks, docs, the token endpoint. */
        List<String> publicPaths,

        @NotBlank String jwtSecret,

        /* How long a verified API key stays cached in Redis. Short, so a revoked key dies quickly. */
        Duration apiKeyCacheTtl,

        @Positive int defaultRequestsPerSecond,

        @Positive int defaultBurstCapacity) {

    public GatewayProperties {
        if (publicPaths == null || publicPaths.isEmpty()) {
            publicPaths = List.of("/actuator/**", "/v1/auth/**", "/__fallback/**");
        }
        if (apiKeyCacheTtl == null) {
            apiKeyCacheTtl = Duration.ofSeconds(60);
        }
    }
}
