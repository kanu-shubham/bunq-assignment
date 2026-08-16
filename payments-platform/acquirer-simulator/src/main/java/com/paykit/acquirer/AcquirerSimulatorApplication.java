package com.paykit.acquirer;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;

/**
 * A stand-in for Visa/Mastercard.
 *
 * <p>It exists so the platform can be run and tested end to end, and — more usefully — so the
 * failure modes can be triggered on demand. Resilience code that has never seen a timeout is
 * a hypothesis, not a feature: this service is how you find out whether the circuit breaker,
 * the retry policy and the reconciliation job actually work.
 */
@SpringBootApplication
@EnableDiscoveryClient
public class AcquirerSimulatorApplication {

    public static void main(String[] args) {
        SpringApplication.run(AcquirerSimulatorApplication.class, args);
    }
}
