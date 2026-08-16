package com.paykit.webhook;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Tells merchants what happened, over HTTP, to servers we do not control.
 *
 * <p>That last clause is the whole design problem. A merchant's endpoint will be down, slow,
 * behind a flaky proxy, or will return 200 after having crashed. So delivery is queued and
 * retried with backoff rather than attempted inline, signed so the merchant can prove the
 * request came from us, and recorded so "we never got the webhook" is an answerable question.
 */
@SpringBootApplication
@org.springframework.boot.context.properties.ConfigurationPropertiesScan
@EnableDiscoveryClient
@EnableScheduling
@EnableJpaAuditing
public class WebhookServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(WebhookServiceApplication.class, args);
    }
}
