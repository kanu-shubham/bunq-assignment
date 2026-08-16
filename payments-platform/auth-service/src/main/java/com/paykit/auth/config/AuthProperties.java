package com.paykit.auth.config;

import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

import java.time.Duration;

@Validated
@ConfigurationProperties(prefix = "paykit.auth")
public record AuthProperties(
        @NotBlank String jwtSecret,
        Duration tokenTtl,
        String issuer,
        /* BCrypt cost factor: each +1 doubles the work. 10 is a sane default; raise it as
           hardware gets faster, lower it in tests so the suite does not crawl. */
        int bcryptStrength) {

    public AuthProperties {
        if (tokenTtl == null) {
            tokenTtl = Duration.ofMinutes(30);
        }
        if (issuer == null || issuer.isBlank()) {
            issuer = "paykit-auth";
        }
        if (bcryptStrength <= 0) {
            bcryptStrength = 10;
        }
    }
}
