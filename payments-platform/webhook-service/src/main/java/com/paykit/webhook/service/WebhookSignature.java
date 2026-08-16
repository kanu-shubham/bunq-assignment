package com.paykit.webhook.service;

import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;

/**
 * Signs outgoing webhooks so the merchant can prove the request came from us.
 *
 * <h3>The threat</h3>
 * A webhook URL is a public HTTP endpoint. Anyone who learns it can POST
 * {@code {"type": "payment_intent.succeeded", "amount": 100000}} to it. If the merchant's
 * integration trusts the body, they have just shipped goods for a payment that never existed.
 *
 * <h3>The header</h3>
 * <pre>
 *   Paykit-Signature: t=1735689600,v1=5257a869e7...
 * </pre>
 * The signed value is {@code "<timestamp>.<body>"}, HMAC-SHA256 with the endpoint's secret.
 *
 * <h3>Why the timestamp is inside the signature</h3>
 * Without it, an attacker who captures one valid request can replay it forever — the signature
 * stays valid because the body has not changed. Including the timestamp in the signed payload
 * means the merchant can reject anything older than a few minutes <em>and</em> the attacker
 * cannot alter the timestamp without breaking the signature. Signing the body alone is the
 * common mistake here.
 *
 * <h3>Why comparison is constant-time</h3>
 * {@code String.equals} returns as soon as two bytes differ, so the time it takes leaks how
 * many leading bytes were correct. Given enough attempts, an attacker recovers a valid
 * signature one byte at a time. {@link MessageDigest#isEqual} always compares the full length.
 */
@Component
public class WebhookSignature {

    private static final String ALGORITHM = "HmacSHA256";
    private static final Duration DEFAULT_TOLERANCE = Duration.ofMinutes(5);

    public String sign(String payload, String secret, Instant timestamp) {
        long epochSeconds = timestamp.getEpochSecond();
        String signed = epochSeconds + "." + payload;
        return "t=%d,v1=%s".formatted(epochSeconds, hmacHex(signed, secret));
    }

    /**
     * The verification a merchant implements on their side. Included here so the contract is
     * executable rather than described in prose, and so it can be covered by tests.
     */
    public boolean verify(String payload, String secret, String header, Instant now) {
        return verify(payload, secret, header, now, DEFAULT_TOLERANCE);
    }

    public boolean verify(String payload, String secret, String header, Instant now, Duration tolerance) {
        if (header == null || header.isBlank()) {
            return false;
        }

        Long timestamp = null;
        String signature = null;
        for (String part : header.split(",")) {
            String[] kv = part.split("=", 2);
            if (kv.length != 2) {
                continue;
            }
            switch (kv[0].trim()) {
                case "t" -> timestamp = parseLong(kv[1].trim());
                case "v1" -> signature = kv[1].trim();
                default -> { /* forward compatibility: ignore unknown scheme versions */ }
            }
        }

        if (timestamp == null || signature == null) {
            return false;
        }

        // Replay window. Also reject timestamps too far in the *future*, which would otherwise
        // let an attacker mint a signature that stays valid indefinitely.
        Duration age = Duration.between(Instant.ofEpochSecond(timestamp), now);
        if (age.abs().compareTo(tolerance) > 0) {
            return false;
        }

        String expected = hmacHex(timestamp + "." + payload, secret);
        return MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                signature.getBytes(StandardCharsets.UTF_8));
    }

    private static String hmacHex(String value, String secret) {
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), ALGORITHM));
            return HexFormat.of().formatHex(mac.doFinal(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception ex) {
            throw new IllegalStateException("Unable to compute webhook signature", ex);
        }
    }

    private static Long parseLong(String value) {
        try {
            return Long.parseLong(value);
        } catch (NumberFormatException ex) {
            return null;
        }
    }
}
