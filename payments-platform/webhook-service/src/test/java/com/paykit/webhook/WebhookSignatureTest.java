package com.paykit.webhook;

import com.paykit.webhook.service.WebhookSignature;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The signature is the only thing standing between a merchant's integration and anyone on
 * the internet who has guessed their webhook URL. These tests are the security contract.
 */
class WebhookSignatureTest {

    private static final String SECRET = "whsec_test_0123456789abcdef";
    private static final String PAYLOAD = """
            {"event_id":"evt_1","type":"payment_intent.succeeded","data":{"amount":10000}}""";

    private WebhookSignature signature;
    private Instant now;

    @BeforeEach
    void setUp() {
        signature = new WebhookSignature();
        now = Instant.parse("2026-03-01T12:00:00Z");
    }

    @Test
    void producesAVerifiableSignature() {
        String header = signature.sign(PAYLOAD, SECRET, now);

        assertThat(header).matches("t=\\d+,v1=[0-9a-f]{64}");
        assertThat(signature.verify(PAYLOAD, SECRET, header, now)).isTrue();
    }

    @Test
    @DisplayName("tampering with the body invalidates the signature")
    void rejectsAModifiedPayload() {
        String header = signature.sign(PAYLOAD, SECRET, now);
        String tampered = PAYLOAD.replace("10000", "1");

        assertThat(signature.verify(tampered, SECRET, header, now)).isFalse();
    }

    @Test
    @DisplayName("a signature from another merchant's secret is rejected")
    void rejectsTheWrongSecret() {
        String header = signature.sign(PAYLOAD, SECRET, now);

        assertThat(signature.verify(PAYLOAD, "whsec_someone_elses_secret", header, now)).isFalse();
    }

    @Test
    @DisplayName("replay protection: an old but otherwise valid signature is rejected")
    void rejectsAReplayedRequest() {
        String header = signature.sign(PAYLOAD, SECRET, now);

        // Captured and replayed ten minutes later. The body and signature are untouched and
        // still match each other — only the timestamp check catches this.
        assertThat(signature.verify(PAYLOAD, SECRET, header, now.plus(Duration.ofMinutes(10)))).isFalse();

        // Still inside the tolerance window.
        assertThat(signature.verify(PAYLOAD, SECRET, header, now.plus(Duration.ofMinutes(2)))).isTrue();
    }

    @Test
    @DisplayName("a timestamp from the future is rejected too")
    void rejectsFutureTimestamps() {
        String header = signature.sign(PAYLOAD, SECRET, now.plus(Duration.ofHours(1)));

        // Without this check, a forged far-future timestamp would produce a signature that
        // stays "fresh" indefinitely.
        assertThat(signature.verify(PAYLOAD, SECRET, header, now)).isFalse();
    }

    @Test
    @DisplayName("the timestamp is inside the signed value, so it cannot be edited")
    void timestampIsCoveredBySignature() {
        String header = signature.sign(PAYLOAD, SECRET, now);
        String signaturePart = header.substring(header.indexOf("v1="));

        // An attacker refreshes the timestamp to defeat the replay window but cannot
        // recompute v1 without the secret.
        String forged = "t=" + now.plus(Duration.ofMinutes(1)).getEpochSecond() + "," + signaturePart;

        assertThat(signature.verify(PAYLOAD, SECRET, forged, now.plus(Duration.ofMinutes(1)))).isFalse();
    }

    @Test
    void rejectsMalformedHeaders() {
        assertThat(signature.verify(PAYLOAD, SECRET, null, now)).isFalse();
        assertThat(signature.verify(PAYLOAD, SECRET, "", now)).isFalse();
        assertThat(signature.verify(PAYLOAD, SECRET, "garbage", now)).isFalse();
        assertThat(signature.verify(PAYLOAD, SECRET, "t=notanumber,v1=abc", now)).isFalse();
        assertThat(signature.verify(PAYLOAD, SECRET, "v1=abc", now)).isFalse();
        assertThat(signature.verify(PAYLOAD, SECRET, "t=1735689600", now)).isFalse();
    }

    @Test
    @DisplayName("unknown scheme versions are ignored, so v2 can be added without breaking v1")
    void toleratesUnknownSchemeVersions() {
        String header = signature.sign(PAYLOAD, SECRET, now) + ",v2=futurescheme";

        assertThat(signature.verify(PAYLOAD, SECRET, header, now)).isTrue();
    }

    @Test
    void signingIsDeterministicForTheSameInputs() {
        assertThat(signature.sign(PAYLOAD, SECRET, now))
                .isEqualTo(signature.sign(PAYLOAD, SECRET, now));
    }
}
