package com.paykit.auth;

import com.paykit.auth.config.AuthProperties;
import com.paykit.auth.domain.ApiKey;
import com.paykit.auth.domain.Merchant;
import com.paykit.auth.service.TokenService;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.SignatureException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Set;

import static io.jsonwebtoken.security.Keys.hmacShaKeyFor;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class TokenServiceTest {

    private static final String SECRET = "a-test-signing-secret-of-at-least-32-bytes!";

    private TokenService tokenService;
    private ApiKey apiKey;

    @BeforeEach
    void setUp() {
        tokenService = new TokenService(
                new AuthProperties(SECRET, Duration.ofMinutes(30), "paykit-auth", 4));

        Merchant merchant = new Merchant("acct_123", "Shop", "s@example.com", "NL");
        merchant.activate();
        apiKey = new ApiKey("sk_id_1", merchant, "sk_test_ABCD", "$2a$04$hash",
                false, Set.of("payments:read", "payments:write"));
    }

    @Test
    void issuesAVerifiableTokenCarryingTheMerchant() {
        TokenService.IssuedToken issued = tokenService.issue(apiKey);

        Claims claims = parse(issued.accessToken(), SECRET);

        assertThat(claims.getSubject()).isEqualTo("acct_123");
        assertThat(claims.getIssuer()).isEqualTo("paykit-auth");
        assertThat(claims.get("kid", String.class)).isEqualTo("sk_id_1");
        assertThat(claims.get("livemode", Boolean.class)).isFalse();
        assertThat(claims.get("scopes", List.class))
                .containsExactlyInAnyOrder("payments:read", "payments:write");
        assertThat(issued.expiresIn()).isEqualTo(1800L);
        assertThat(issued.tokenType()).isEqualTo("Bearer");
    }

    @Test
    @DisplayName("a token signed with a different secret does not verify")
    void rejectsForgedSignatures() {
        String token = tokenService.issue(apiKey).accessToken();

        assertThatThrownBy(() -> parse(token, "a-completely-different-secret-32-bytes-long"))
                .isInstanceOf(SignatureException.class);
    }

    @Test
    @DisplayName("the token carries the configured TTL, which is what bounds the revocation window")
    void tokensExpire() {
        // A 1-second TTL would make this test a race: on a slow machine the token expires
        // between being issued and being parsed, and parsing an expired token throws. Assert
        // the exp - iat *interval* instead — that is the property under test, and it holds
        // regardless of how long the assertion takes to run.
        TokenService shortLived = new TokenService(
                new AuthProperties(SECRET, Duration.ofMinutes(2), "paykit-auth", 4));

        Claims claims = parse(shortLived.issue(apiKey).accessToken(), SECRET);

        assertThat(claims.getExpiration()).isAfter(claims.getIssuedAt());
        assertThat(claims.getExpiration().getTime() - claims.getIssuedAt().getTime())
                .isEqualTo(Duration.ofMinutes(2).toMillis());
    }

    private static Claims parse(String token, String secret) {
        SecretKey key = hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
        return Jwts.parser().verifyWith(key).build().parseSignedClaims(token).getPayload();
    }
}
