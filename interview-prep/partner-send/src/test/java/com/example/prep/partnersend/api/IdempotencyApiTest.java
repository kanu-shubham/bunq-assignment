package com.example.prep.partnersend.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.prep.partnersend.domain.TransferRepository;
import com.example.prep.partnersend.idempotency.IdempotencyRepository;
import com.example.prep.partnersend.idempotency.IdempotencyService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/**
 * The idempotency contract, exercised through real HTTP.
 *
 * <p>Each test here corresponds to a question an interviewer is likely to ask, and the
 * assertion is the answer.
 */
@SpringBootTest
@AutoConfigureMockMvc
class IdempotencyApiTest {

    private static final String PARTNER = "acme-bank";
    private static final String URL = "/v1/partners/" + PARTNER + "/transfers";

    private static final String BODY = """
            {"partnerReference":"ACME-001","amountMinorUnits":"25000",
             "currency":"GBP","beneficiaryIban":"GB33BUKB20201555555555"}""";

    private static final String DIFFERENT_BODY = """
            {"partnerReference":"ACME-001","amountMinorUnits":"9900000",
             "currency":"GBP","beneficiaryIban":"GB33BUKB20201555555555"}""";

    @Autowired MockMvc mockMvc;
    @Autowired TransferRepository transfers;
    @Autowired IdempotencyRepository idempotencyRecords;
    @Autowired IdempotencyService idempotencyService;
    @Autowired ObjectMapper objectMapper;

    @BeforeEach
    void clean() {
        transfers.deleteAll();
        idempotencyRecords.deleteAll();
    }

    @Test
    @DisplayName("first request is accepted and creates exactly one transfer")
    void firstRequestCreates() throws Exception {
        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("RECEIVED"))
                .andExpect(jsonPath("$.amountMinorUnits").value("25000"));

        assertThat(transfers.count()).isEqualTo(1);
    }

    @Test
    @DisplayName("replaying the same key returns the identical response and creates nothing new")
    void replayIsByteIdentical() throws Exception {
        String first = mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-2")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isAccepted())
                .andReturn().getResponse().getContentAsString();

        String second = mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-2")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isAccepted())
                .andExpect(header().string("Idempotent-Replay", "true"))
                .andReturn().getResponse().getContentAsString();

        // Same body means the same transfer id — the client cannot tell the difference,
        // which is precisely the guarantee being sold.
        assertThat(second).isEqualTo(first);
        assertThat(transfers.count()).isEqualTo(1);
    }

    @Test
    @DisplayName("whitespace and key order do not make an identical request look different")
    void canonicalisesBeforeHashing() throws Exception {
        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-3")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isAccepted());

        String reordered = """
                {"currency":"GBP",
                   "beneficiaryIban":"GB33BUKB20201555555555",
                 "partnerReference":"ACME-001","amountMinorUnits":"25000"}""";

        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-3")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(reordered))
                .andExpect(status().isAccepted())
                .andExpect(header().string("Idempotent-Replay", "true"));

        assertThat(transfers.count()).isEqualTo(1);
    }

    @Test
    @DisplayName("reusing a key for a different payment is a 422, never a silent replay")
    void keyReuseWithDifferentBodyIsRejected() throws Exception {
        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-4")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isAccepted());

        // If this returned the first response, the client would believe a £99,000 payment
        // had been accepted when in fact a £250 one had.
        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-4")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(DIFFERENT_BODY))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("idempotency_key_reuse"));

        assertThat(transfers.count()).isEqualTo(1);
    }

    @Test
    @DisplayName("a duplicate arriving while the first is still running gets 409, not a guess")
    void inFlightDuplicateIsToldToRetry() throws Exception {
        // Claim the key without completing it — exactly the state a concurrent duplicate
        // would find while the original request is mid-flight. The claim must use the same
        // canonical form the controller hashes, otherwise this exercises the hash-mismatch
        // branch (422) instead of the in-flight one.
        String canonical = objectMapper.writeValueAsString(
                objectMapper.readValue(BODY, CreateTransferRequest.class));
        var claim = idempotencyService.claim(PARTNER, "key-5", canonical);
        assertThat(claim).isInstanceOf(IdempotencyService.Claim.Acquired.class);

        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-5")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isConflict())
                .andExpect(header().string("Retry-After", "1"))
                .andExpect(jsonPath("$.code").value("request_in_flight"));

        assertThat(transfers.count()).isZero();
    }

    @Test
    @DisplayName("the same key from a different partner is a different key")
    void keysAreScopedPerPartner() throws Exception {
        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "shared-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isAccepted());

        mockMvc.perform(post("/v1/partners/other-bank/transfers")
                        .header("Idempotency-Key", "shared-key")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(BODY))
                .andExpect(status().isAccepted());

        // Two partners choosing "1" as their first key must not collide.
        assertThat(transfers.count()).isEqualTo(2);
    }

    @Test
    void missingIdempotencyKeyIsRejected() throws Exception {
        mockMvc.perform(post(URL).contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("missing_header"));
    }

    @Test
    void invalidPayloadIsRejectedBeforeAnythingIsClaimed() throws Exception {
        String badAmount = """
                {"partnerReference":"ACME-001","amountMinorUnits":"12.34",
                 "currency":"GBP","beneficiaryIban":"GB33BUKB20201555555555"}""";

        mockMvc.perform(post(URL)
                        .header("Idempotency-Key", "key-6")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(badAmount))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("invalid_request"));

        // Validation runs before the claim, so a malformed request does not burn the key.
        assertThat(idempotencyRecords.count()).isZero();
    }
}
