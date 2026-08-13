package com.example.prep.partnersend.config;

import com.example.prep.partnersend.outbox.EventPublisher;
import com.example.prep.partnersend.partner.PartnerBankClient;
import com.example.prep.partnersend.partner.SimulatedPartnerBankClient;
import java.time.Clock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class ApplicationConfig {

    private static final Logger log = LoggerFactory.getLogger(ApplicationConfig.class);

    /**
     * Injecting a {@link Clock} instead of calling {@code Instant.now()} inside domain code
     * is a small discipline with a large payoff: time becomes a value you can control, so
     * "expire this claim after 30 seconds" is a test that runs in microseconds rather than
     * one that sleeps. It is the same instinct as the injected {@code submitFeedback} seam
     * in the bunq widget — push the untestable thing to the edge.
     */
    @Bean
    public Clock clock() {
        return Clock.systemUTC();
    }

    /** Named so {@link ResilienceConfig} can wrap it explicitly. */
    @Bean("rawPartnerBankClient")
    public PartnerBankClient rawPartnerBankClient() {
        // 15% unavailable, 10% slow, 5% rejected — enough to see the breaker work.
        return new SimulatedPartnerBankClient(0.15, 0.10, 0.05);
    }

    /** Stands in for Kafka. Replace with a real producer and nothing else changes. */
    @Bean
    public EventPublisher eventPublisher() {
        return event -> log.info("PUBLISH type={} aggregate={} eventId={} payload={}",
                event.getEventType(), event.getAggregateId(), event.getEventId(), event.getPayload());
    }
}
