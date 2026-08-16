package com.paykit.webhook.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

@ConfigurationProperties(prefix = "paykit.webhooks")
public record WebhookProperties(
        int maxAttempts,
        int batchSize,
        int disableAfterConsecutiveFailures,
        Duration requestTimeout) {

    public WebhookProperties {
        if (maxAttempts <= 0) {
            maxAttempts = 8;      // ~10s to ~1h of retries
        }
        if (batchSize <= 0) {
            batchSize = 50;
        }
        if (disableAfterConsecutiveFailures <= 0) {
            disableAfterConsecutiveFailures = 50;
        }
        if (requestTimeout == null) {
            // Merchants' servers are not ours to trust with our threads.
            requestTimeout = Duration.ofSeconds(10);
        }
    }
}
