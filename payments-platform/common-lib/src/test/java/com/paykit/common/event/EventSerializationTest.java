package com.paykit.common.event;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Proves the wire contract: a sealed event survives a Kafka round trip with its concrete
 * type intact, and the generic envelope needs a {@code TypeReference} because of erasure.
 */
class EventSerializationTest {

    private ObjectMapper mapper;

    @BeforeEach
    void setUp() {
        mapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
                .disable(com.fasterxml.jackson.databind.SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    @Test
    void polymorphicEventKeepsItsConcreteTypeAcrossTheWire() throws Exception {
        PaymentEvent original = new PaymentEvents.PaymentSucceeded(
                "evt_1", "acct_1", "pi_1", "ch_1",
                Money.of(10_000L, Currency.USD), Money.of(320L, Currency.USD),
                "pm_1", "visa", "4242", "acq_ref_1", Instant.parse("2026-01-15T10:00:00Z"));

        String json = mapper.writeValueAsString(original);
        assertThat(json).contains("\"type\":\"payment_intent.succeeded\"");

        PaymentEvent revived = mapper.readValue(json, PaymentEvent.class);

        assertThat(revived)
                .isInstanceOf(PaymentEvents.PaymentSucceeded.class)
                .isEqualTo(original);
    }

    @Test
    void moneySerialisesAsMinorUnitsPlusCurrency() throws Exception {
        String json = mapper.writeValueAsString(Money.of(1999L, Currency.EUR));

        assertThat(json).isEqualTo("{\"amount\":1999,\"currency\":\"EUR\"}");
        assertThat(mapper.readValue(json, Money.class)).isEqualTo(Money.of(1999L, Currency.EUR));
    }

    @Test
    void genericEnvelopeNeedsATypeReferenceBecauseOfErasure() throws Exception {
        PaymentEvents.PaymentFailed failed = new PaymentEvents.PaymentFailed(
                "evt_2", "acct_1", "pi_2", Money.of(2500L, Currency.USD),
                "insufficient_funds", "Your card has insufficient funds.", 1, Instant.now());

        String json = mapper.writeValueAsString(EventEnvelope.wrap(failed, "req_abc"));

        EventEnvelope<PaymentEvent> revived = mapper.readValue(json, new TypeReference<>() {});

        assertThat(revived.correlationId()).isEqualTo("req_abc");
        assertThat(revived.apiVersion()).isEqualTo(EventEnvelope.CURRENT_API_VERSION);
        assertThat(revived.data()).isEqualTo(failed);
    }

    @Test
    void netAmountSubtractsTheProcessingFee() {
        PaymentEvents.PaymentSucceeded event = new PaymentEvents.PaymentSucceeded(
                "evt_3", "acct_1", "pi_3", "ch_3",
                Money.of(10_000L, Currency.USD), Money.of(320L, Currency.USD),
                "pm_1", "visa", "4242", "acq_1", Instant.now());

        assertThat(event.netAmount()).isEqualTo(Money.of(9_680L, Currency.USD));
    }
}
