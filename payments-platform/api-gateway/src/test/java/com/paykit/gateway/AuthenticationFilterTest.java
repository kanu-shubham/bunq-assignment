package com.paykit.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.paykit.gateway.auth.Principal;
import com.paykit.gateway.auth.TokenVerifier;
import com.paykit.gateway.config.GatewayProperties;
import com.paykit.gateway.filter.AuthenticationFilter;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.test.StepVerifier;

import java.time.Duration;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * The gateway's security contract, tested without starting a server.
 *
 * <p>{@code MockServerWebExchange} lets us drive a reactive filter directly, and
 * {@code StepVerifier} asserts on the resulting {@code Mono} — the reactive equivalent of
 * asserting on a return value.
 */
class AuthenticationFilterTest {

    private TokenVerifier tokenVerifier;
    private AuthenticationFilter filter;
    private AtomicReference<ServerWebExchange> forwarded;
    private GatewayFilterChain chain;

    @BeforeEach
    void setUp() {
        tokenVerifier = mock(TokenVerifier.class);
        GatewayProperties properties = new GatewayProperties(
                List.of("/actuator/**", "/v1/auth/**"),
                "test-secret-that-is-at-least-32-bytes-long",
                Duration.ofSeconds(60), 50, 100);
        // Build the mapper the way Spring Boot auto-configures it. A bare `new ObjectMapper()`
        // cannot serialise java.time.Instant, so the filter would quietly fall back to a stub
        // body and the test would pass without ever exercising the real error path.
        ObjectMapper objectMapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
                .disable(com.fasterxml.jackson.databind.SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);

        filter = new AuthenticationFilter(tokenVerifier, properties, objectMapper);

        forwarded = new AtomicReference<>();
        chain = exchange -> {
            forwarded.set(exchange);
            return Mono.empty();
        };
    }

    @Test
    @DisplayName("a valid credential is replaced by a trusted merchant header")
    void stampsMerchantHeaderOnSuccess() {
        when(tokenVerifier.verify(anyString()))
                .thenReturn(Mono.just(new Principal("acct_real", "sk_1", false, Set.of("*"))));

        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/v1/payment_intents")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer sk_test_valid"));

        StepVerifier.create(filter.filter(exchange, chain)).verifyComplete();

        HttpHeaders headers = forwarded.get().getRequest().getHeaders();
        assertThat(headers.getFirst(AuthenticationFilter.MERCHANT_HEADER)).isEqualTo("acct_real");
        assertThat(headers.getFirst(AuthenticationFilter.KEY_ID_HEADER)).isEqualTo("sk_1");
        assertThat(headers.getFirst(AuthenticationFilter.LIVEMODE_HEADER)).isEqualTo("false");
    }

    /**
     * THE ONE THAT MATTERS. Downstream services trust X-Merchant-Id blindly, so if a client
     * can set it themselves they can read any merchant's payments. This test is the guard rail.
     */
    @Test
    @DisplayName("a client-supplied merchant header cannot be used to impersonate another tenant")
    void stripsSpoofedMerchantHeader() {
        when(tokenVerifier.verify(anyString()))
                .thenReturn(Mono.just(new Principal("acct_real", "sk_1", false, Set.of("*"))));

        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/v1/payment_intents")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer sk_test_valid")
                        .header(AuthenticationFilter.MERCHANT_HEADER, "acct_victim")
                        .header(AuthenticationFilter.LIVEMODE_HEADER, "true"));

        StepVerifier.create(filter.filter(exchange, chain)).verifyComplete();

        HttpHeaders headers = forwarded.get().getRequest().getHeaders();
        assertThat(headers.getFirst(AuthenticationFilter.MERCHANT_HEADER)).isEqualTo("acct_real");
        assertThat(headers.getFirst(AuthenticationFilter.LIVEMODE_HEADER)).isEqualTo("false");
    }

    @Test
    @DisplayName("public paths are forwarded, but still have identity headers stripped")
    void publicPathsSkipAuthenticationButNotStripping() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.post("/v1/auth/token")
                        .header(AuthenticationFilter.MERCHANT_HEADER, "acct_victim"));

        StepVerifier.create(filter.filter(exchange, chain)).verifyComplete();

        assertThat(forwarded.get().getRequest().getHeaders().getFirst(AuthenticationFilter.MERCHANT_HEADER))
                .isNull();
    }

    @Test
    void rejectsMissingAuthorizationHeaderWith401() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/v1/payment_intents"));

        StepVerifier.create(filter.filter(exchange, chain)).verifyComplete();

        assertThat(forwarded.get()).isNull();
        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        assertThat(exchange.getResponse().getHeaders().getFirst(HttpHeaders.WWW_AUTHENTICATE))
                .contains("Bearer");

        // The body must be the platform's standard error envelope, fully serialised —
        // including the Instant timestamp, which is what the fallback used to hide.
        assertThat(exchange.getResponse().getBodyAsString().block())
                .contains("\"code\":\"authentication_required\"")
                .contains("\"timestamp\"");
    }

    @Test
    void rejectsAnInvalidCredentialWith401() {
        when(tokenVerifier.verify(anyString())).thenReturn(Mono.empty());

        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/v1/payment_intents")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer sk_test_revoked"));

        StepVerifier.create(filter.filter(exchange, chain)).verifyComplete();

        assertThat(forwarded.get()).isNull();
        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    @Test
    void rejectsNonBearerSchemes() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/v1/payment_intents")
                        .header(HttpHeaders.AUTHORIZATION, "Basic dXNlcjpwYXNz"));

        StepVerifier.create(filter.filter(exchange, chain)).verifyComplete();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }
}
