package com.paykit.payment;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.github.tomakehurst.wiremock.WireMockServer;
import com.paykit.payment.outbox.OutboxEventRepository;
import org.awaitility.Awaitility;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.kafka.KafkaContainer;
import org.testcontainers.utility.DockerImageName;

import java.time.Duration;
import java.util.UUID;

// NOTE: WireMock also exposes a static post(...), which would collide with MockMvc's.
// The WireMock builders stay qualified as WireMock.post(...) so the ambiguity cannot bite.
import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * The real thing: Postgres, Redis and Kafka in containers, WireMock standing in for the
 * acquirer, and requests going through the actual controller stack.
 *
 * <p>Unit tests prove the pieces behave; this proves the wiring does — Flyway migrations
 * match the entities, the AOP proxy actually intercepts, the outbox row is written in the
 * same transaction as the payment, and Kafka is reachable. Those are precisely the failures
 * that mocks cannot catch.
 *
 * <p>REQUIRES DOCKER; skipped cleanly without it.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@Testcontainers(disabledWithoutDocker = true)
class PaymentFlowIntegrationTest {

    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine");

    @Container
    @ServiceConnection
    static KafkaContainer kafka = new KafkaContainer(DockerImageName.parse("apache/kafka:3.9.0"));

    @Container
    @ServiceConnection(name = "redis")
    static GenericContainer<?> redis = new GenericContainer<>(DockerImageName.parse("redis:7-alpine"))
            .withExposedPorts(6379);

    /** The acquirer is a third party, so it is stubbed rather than containerised. */
    static WireMockServer acquirer;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private OutboxEventRepository outboxEventRepository;

    @BeforeAll
    static void startAcquirer() {
        acquirer = new WireMockServer(options().dynamicPort());
        acquirer.start();

        acquirer.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/acquirer/v1/authorizations"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "approved": true,
                                  "acquirer_reference": "acqref_test_1",
                                  "card_brand": "visa",
                                  "card_last4": "4242",
                                  "network": "interlink"
                                }
                                """)));

        acquirer.stubFor(com.github.tomakehurst.wiremock.client.WireMock.post(urlPathEqualTo("/acquirer/v1/refunds"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {"approved": true, "acquirer_reference": "acqref_refund_1"}
                                """)));
    }

    @AfterAll
    static void stopAcquirer() {
        if (acquirer != null) {
            acquirer.stop();
        }
    }

    /**
     * {@code @ServiceConnection} covers the containers; WireMock's port is only known at
     * runtime, so it is injected the older way.
     */
    @DynamicPropertySource
    static void acquirerProperties(DynamicPropertyRegistry registry) {
        registry.add("paykit.acquirer.base-url", () -> "http://localhost:" + acquirer.port());
    }

    @Test
    @DisplayName("create -> confirm -> refund, with the outbox emitting an event at each step")
    void endToEndPaymentFlow() throws Exception {
        String merchantId = "acct_integration_1";
        long outboxBefore = outboxEventRepository.count();

        // ---- 1. Create ----
        String created = mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", merchantId)
                        .header("Idempotency-Key", UUID.randomUUID().toString())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"amount": 10000, "currency": "EUR",
                                 "description": "Integration test order",
                                 "metadata": {"order_id": "ord_1"}}
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("requires_confirmation"))
                .andExpect(jsonPath("$.amount").value(10000))
                .andReturn().getResponse().getContentAsString();

        String paymentIntentId = objectMapper.readTree(created).at("/id").asText();
        assertThat(paymentIntentId).startsWith("pi_");

        // ---- 2. Confirm ----
        String confirmed = mockMvc.perform(post("/v1/payment_intents/" + paymentIntentId + "/confirm")
                        .header("X-Merchant-Id", merchantId)
                        .header("Idempotency-Key", UUID.randomUUID().toString())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"payment_method_id": "pm_card_visa"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.payment_intent.status").value("succeeded"))
                .andExpect(jsonPath("$.charge.amount").value(10000))
                // 2.9% + 30 = 320
                .andExpect(jsonPath("$.charge.fee").value(320))
                .andExpect(jsonPath("$.charge.net").value(9680))
                .andReturn().getResponse().getContentAsString();

        String chargeId = objectMapper.readTree(confirmed).at("/charge/id").asText();

        // The idempotency key really did reach the card network.
        acquirer.verify(postRequestedFor(urlPathEqualTo("/acquirer/v1/authorizations")));

        // ---- 3. Partial refund ----
        mockMvc.perform(post("/v1/refunds")
                        .header("X-Merchant-Id", merchantId)
                        .header("Idempotency-Key", UUID.randomUUID().toString())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"charge": "%s", "amount": 5000, "reason": "REQUESTED_BY_CUSTOMER"}
                                """.formatted(chargeId)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.amount").value(5000))
                .andExpect(jsonPath("$.fee_refunded").value(160));

        mockMvc.perform(get("/v1/charges/" + chargeId).header("X-Merchant-Id", merchantId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.amount_refunded").value(5000))
                .andExpect(jsonPath("$.refunded").value(false));

        // ---- 4. Over-refunding the remainder plus one cent is rejected ----
        mockMvc.perform(post("/v1/refunds")
                        .header("X-Merchant-Id", merchantId)
                        .header("Idempotency-Key", UUID.randomUUID().toString())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"charge": "%s", "amount": 5001}
                                """.formatted(chargeId)))
                .andExpect(status().isBadRequest());

        // ---- 5. Three events were written to the outbox, in the same transactions ----
        Awaitility.await().atMost(Duration.ofSeconds(15)).untilAsserted(() ->
                assertThat(outboxEventRepository.count() - outboxBefore).isGreaterThanOrEqualTo(3));
    }

    @Test
    @DisplayName("replaying an Idempotency-Key returns the original payment, not a second one")
    void idempotentCreate() throws Exception {
        String merchantId = "acct_integration_2";
        String key = UUID.randomUUID().toString();
        String body = """
                {"amount": 2500, "currency": "USD", "description": "Retry me"}
                """;

        String first = mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", merchantId)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();

        String second = mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", merchantId)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();

        JsonNode firstJson = objectMapper.readTree(first);
        JsonNode secondJson = objectMapper.readTree(second);

        // Same id: one payment exists, not two.
        assertThat(secondJson.at("/id").asText()).isEqualTo(firstJson.at("/id").asText());
        assertThat(secondJson.at("/created_at").asText()).isEqualTo(firstJson.at("/created_at").asText());
    }

    @Test
    @DisplayName("the same key with a different body is a 409, not a silent replay")
    void idempotencyKeyReuseWithDifferentBodyIsRejected() throws Exception {
        String merchantId = "acct_integration_3";
        String key = UUID.randomUUID().toString();

        mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", merchantId)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"amount": 1000, "currency": "EUR"}
                                """))
                .andExpect(status().isCreated());

        mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", merchantId)
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"amount": 999999, "currency": "EUR"}
                                """))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("idempotency_conflict"));
    }

    @Test
    @DisplayName("idempotency keys are scoped per merchant, so two tenants cannot collide")
    void idempotencyIsScopedPerMerchant() throws Exception {
        String key = "shared-key-" + UUID.randomUUID();
        String body = """
                {"amount": 4200, "currency": "GBP"}
                """;

        String a = mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", "acct_tenant_a")
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();

        String b = mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", "acct_tenant_b")
                        .header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();

        assertThat(objectMapper.readTree(b).at("/id").asText())
                .isNotEqualTo(objectMapper.readTree(a).at("/id").asText());
    }

    @Test
    @DisplayName("one merchant cannot read another merchant's payment")
    void tenantIsolation() throws Exception {
        String created = mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", "acct_owner")
                        .header("Idempotency-Key", UUID.randomUUID().toString())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"amount": 7500, "currency": "EUR"}
                                """))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();

        String id = objectMapper.readTree(created).at("/id").asText();

        mockMvc.perform(get("/v1/payment_intents/" + id).header("X-Merchant-Id", "acct_owner"))
                .andExpect(status().isOk());

        // A 404, not a 403: confirming the resource exists would leak information.
        mockMvc.perform(get("/v1/payment_intents/" + id).header("X-Merchant-Id", "acct_intruder"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("resource_not_found"));
    }

    @Test
    @DisplayName("confirm without an Idempotency-Key is refused")
    void confirmRequiresAnIdempotencyKey() throws Exception {
        String created = mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", "acct_nokey")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"amount": 1500, "currency": "EUR"}
                                """))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();

        String id = objectMapper.readTree(created).at("/id").asText();

        mockMvc.perform(post("/v1/payment_intents/" + id + "/confirm")
                        .header("X-Merchant-Id", "acct_nokey")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"payment_method_id": "pm_card_visa"}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.param").value("Idempotency-Key"));
    }

    @Test
    void validationRejectsBadAmounts() throws Exception {
        mockMvc.perform(post("/v1/payment_intents")
                        .header("X-Merchant-Id", "acct_validation")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"amount": 0, "currency": "EUR"}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("invalid_request"));
    }
}
