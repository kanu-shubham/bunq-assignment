package com.paykit.ledger;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;

/**
 * The books.
 *
 * <p>This service never handles an HTTP request that moves money. It listens to Kafka, and
 * every payment fact becomes a balanced set of ledger postings. Keeping accounting out of the
 * payment path means a slow or broken ledger cannot stop customers paying — and, just as
 * importantly, that the ledger can be rebuilt from the event log if it is ever wrong.
 */
@SpringBootApplication
@EnableDiscoveryClient
@EnableJpaAuditing
public class LedgerServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(LedgerServiceApplication.class, args);
    }
}
