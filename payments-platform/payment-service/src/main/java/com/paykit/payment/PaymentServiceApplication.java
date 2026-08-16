package com.paykit.payment;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * The core service: payment intents, charges, refunds.
 *
 * <p>Enabled here:
 * <ul>
 *   <li>{@code @EnableScheduling} — the outbox publisher and the reconciliation job.</li>
 *   <li>{@code @EnableJpaAuditing} — populates {@code createdAt} / {@code updatedAt}.</li>
 *   <li>{@code @EnableDiscoveryClient} — registers with Eureka so the gateway can find it.</li>
 *   <li>{@code @ConfigurationPropertiesScan} — binds the {@code paykit.*} property records.</li>
 * </ul>
 *
 * <p>AOP is not enabled explicitly: {@code spring-boot-starter-aop} on the classpath is enough
 * for {@code @Aspect} beans such as {@code IdempotencyAspect} to be applied.
 */
@SpringBootApplication
@EnableDiscoveryClient
@EnableScheduling
@EnableJpaAuditing
@ConfigurationPropertiesScan
public class PaymentServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(PaymentServiceApplication.class, args);
    }
}
