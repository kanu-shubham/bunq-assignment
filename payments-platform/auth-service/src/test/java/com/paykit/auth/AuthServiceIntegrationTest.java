package com.paykit.auth;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * End-to-end through the real stack: HTTP -> controller -> service -> Hibernate -> Postgres.
 *
 * <h3>Why Testcontainers instead of H2</h3>
 * H2 in "Postgres compatibility mode" is not Postgres. It has no {@code TIMESTAMPTZ}, no
 * partial indexes, different upsert syntax and different locking. A suite that passes on H2
 * and fails on Postgres has tested the wrong database — and you find out in production.
 * Testcontainers starts the real thing in Docker and throws it away afterwards.
 *
 * <h3>{@code @ServiceConnection}</h3>
 * Spring Boot 3.1+ reads the container's actual host and (randomly assigned) port and points
 * the DataSource at it. No {@code @DynamicPropertySource} plumbing.
 *
 * <p>REQUIRES DOCKER. {@code disabledWithoutDocker = true} skips this class cleanly when the
 * daemon is not available, so the build stays green on machines without it.
 */
@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
@Testcontainers(disabledWithoutDocker = true)
class AuthServiceIntegrationTest {

    /**
     * {@code static} matters: one container is started for the whole class rather than one
     * per test method. Container startup is measured in seconds; test methods should not be.
     */
    @Container
    @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine");

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    @DisplayName("register -> receive a key -> exchange it for a token -> use the token")
    void fullOnboardingFlow() throws Exception {
        // 1. Register. Flyway has already created the schema inside the container.
        String registerResponse = mockMvc.perform(post("/v1/auth/merchants")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name": "Acme Coffee", "email": "acme@example.com", "country_code": "NL"}
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.merchant.id").exists())
                .andExpect(jsonPath("$.merchant.status").value("active"))
                .andExpect(jsonPath("$.api_key.secret").exists())
                // The hash must never appear in a response.
                .andExpect(jsonPath("$.api_key.key.secret_hash").doesNotExist())
                .andReturn().getResponse().getContentAsString();

        JsonNode registered = objectMapper.readTree(registerResponse);
        String merchantId = registered.at("/merchant/id").asText();
        String secret = registered.at("/api_key/secret").asText();

        assertThat(merchantId).startsWith("acct_");
        assertThat(secret).startsWith("sk_test_");

        // 2. Exchange the key for a JWT.
        String tokenResponse = mockMvc.perform(post("/v1/auth/token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(java.util.Map.of("api_key", secret))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.access_token").exists())
                .andExpect(jsonPath("$.token_type").value("Bearer"))
                .andReturn().getResponse().getContentAsString();

        assertThat(objectMapper.readTree(tokenResponse).at("/access_token").asText())
                .isNotBlank()
                .contains(".");

        // 3. The gateway's internal verification endpoint resolves the key to this merchant.
        mockMvc.perform(post("/internal/api_keys/verify")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(java.util.Map.of("api_key", secret))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.merchant_id").value(merchantId));

        // 4. The merchant can read itself...
        mockMvc.perform(get("/v1/auth/merchants/" + merchantId)
                        .header("X-Merchant-Id", merchantId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email").value("acme@example.com"));

        // 5. ...but not anybody else. Tenant isolation, enforced in the service.
        mockMvc.perform(get("/v1/auth/merchants/acct_someone_else")
                        .header("X-Merchant-Id", merchantId))
                .andExpect(status().isNotFound());
    }

    @Test
    @DisplayName("a revoked key stops working immediately")
    void revocationTakesEffect() throws Exception {
        String registerResponse = mockMvc.perform(post("/v1/auth/merchants")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name": "Revoke Test", "email": "revoke@example.com", "country_code": "DE"}
                                """))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();

        JsonNode registered = objectMapper.readTree(registerResponse);
        String merchantId = registered.at("/merchant/id").asText();
        String keyId = registered.at("/api_key/key/id").asText();
        String secret = registered.at("/api_key/secret").asText();

        mockMvc.perform(post("/v1/auth/token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(java.util.Map.of("api_key", secret))))
                .andExpect(status().isOk());

        mockMvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .delete("/v1/auth/api_keys/" + keyId)
                        .header("X-Merchant-Id", merchantId))
                .andExpect(status().isNoContent());

        mockMvc.perform(post("/v1/auth/token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(java.util.Map.of("api_key", secret))))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("authentication_required"));
    }

    @Test
    @DisplayName("bean validation rejects a bad payload before any business logic runs")
    void validationFailuresAreReportedPerField() throws Exception {
        mockMvc.perform(post("/v1/auth/merchants")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"name": "", "email": "not-an-email", "country_code": "netherlands"}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("invalid_request"))
                .andExpect(jsonPath("$.error.violations").isArray())
                .andExpect(jsonPath("$.error.violations.length()").value(3));
    }

    @Test
    void duplicateEmailIsRejected() throws Exception {
        String body = """
                {"name": "First", "email": "dupe@example.com", "country_code": "FR"}
                """;

        mockMvc.perform(post("/v1/auth/merchants").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated());

        mockMvc.perform(post("/v1/auth/merchants").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.param").value("email"));
    }
}
