package com.paykit.gateway.filter;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.paykit.common.api.ApiError;
import com.paykit.common.api.ErrorCode;
import com.paykit.gateway.auth.Principal;
import com.paykit.gateway.auth.TokenVerifier;
import com.paykit.gateway.config.GatewayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.util.AntPathMatcher;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Authenticates once, at the edge, and tells the services behind it who is calling.
 *
 * <h3>The trust model</h3>
 * Internal services do <b>not</b> re-validate credentials. They trust the
 * {@code X-Merchant-Id} header. That is only safe because of two rules:
 * <ol>
 *   <li>Services are not reachable from outside the cluster — the only route in is this gateway.</li>
 *   <li>This filter <b>strips</b> any client-supplied identity header before setting its own.
 *       Skip that and anyone can send {@code X-Merchant-Id: acct_victim} and read another
 *       merchant's payments. It is a one-line mistake with a catastrophic blast radius, and
 *       it is the single most important line in this class.</li>
 * </ol>
 *
 * <p>In a zero-trust deployment you would go further and give each hop a signed, short-lived
 * internal token (mTLS or a service JWT), so a compromised pod still cannot impersonate a merchant.
 */
@Component
public class AuthenticationFilter implements GlobalFilter, Ordered {

    private static final Logger log = LoggerFactory.getLogger(AuthenticationFilter.class);

    public static final String MERCHANT_HEADER = "X-Merchant-Id";
    public static final String KEY_ID_HEADER = "X-Api-Key-Id";
    public static final String LIVEMODE_HEADER = "X-Livemode";

    private static final AntPathMatcher PATH_MATCHER = new AntPathMatcher();

    private final TokenVerifier tokenVerifier;
    private final GatewayProperties properties;
    private final ObjectMapper objectMapper;

    public AuthenticationFilter(TokenVerifier tokenVerifier,
                                GatewayProperties properties,
                                ObjectMapper objectMapper) {
        this.tokenVerifier = tokenVerifier;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String path = exchange.getRequest().getURI().getPath();

        if (isPublic(path)) {
            return chain.filter(stripClientIdentityHeaders(exchange));
        }

        String credential = extractBearerToken(exchange.getRequest().getHeaders());
        if (credential == null) {
            return unauthorized(exchange, "Missing Authorization header. "
                    + "Send 'Authorization: Bearer <api key or token>'.");
        }

        return tokenVerifier.verify(credential)
                .flatMap(principal -> chain.filter(withPrincipal(exchange, principal)))
                // verify() returns an empty Mono for any invalid credential, so this is the
                // single place a rejection is turned into a response.
                .switchIfEmpty(Mono.defer(() -> unauthorized(exchange,
                        "The provided credential is invalid, expired or revoked.")));
    }

    private boolean isPublic(String path) {
        return properties.publicPaths().stream().anyMatch(pattern -> PATH_MATCHER.match(pattern, path));
    }

    private static String extractBearerToken(HttpHeaders headers) {
        String header = headers.getFirst(HttpHeaders.AUTHORIZATION);
        if (header == null || header.isBlank()) {
            return null;
        }
        if (header.regionMatches(true, 0, "Bearer ", 0, 7)) {
            String value = header.substring(7).trim();
            return value.isEmpty() ? null : value;
        }
        return null;
    }

    /** Replaces any inbound identity headers with the ones we just proved. */
    private ServerWebExchange withPrincipal(ServerWebExchange exchange, Principal principal) {
        ServerHttpRequest request = exchange.getRequest().mutate()
                .headers(headers -> {
                    removeSpoofableHeaders(headers);
                    headers.set(MERCHANT_HEADER, principal.merchantId());
                    headers.set(KEY_ID_HEADER, principal.keyId());
                    headers.set(LIVEMODE_HEADER, Boolean.toString(principal.livemode()));
                })
                .build();
        return exchange.mutate().request(request).build();
    }

    /** Even on a public path, a client must not be able to inject an identity. */
    private ServerWebExchange stripClientIdentityHeaders(ServerWebExchange exchange) {
        ServerHttpRequest request = exchange.getRequest().mutate()
                .headers(AuthenticationFilter::removeSpoofableHeaders)
                .build();
        return exchange.mutate().request(request).build();
    }

    private static void removeSpoofableHeaders(HttpHeaders headers) {
        headers.remove(MERCHANT_HEADER);
        headers.remove(KEY_ID_HEADER);
        headers.remove(LIVEMODE_HEADER);
    }

    private Mono<Void> unauthorized(ServerWebExchange exchange, String message) {
        ServerHttpResponse response = exchange.getResponse();
        response.setStatusCode(ErrorCode.AUTHENTICATION_REQUIRED.status());
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        // Correct 401 semantics: tell the client which scheme to use.
        response.getHeaders().set(HttpHeaders.WWW_AUTHENTICATE, "Bearer realm=\"paykit\"");

        String correlationId = exchange.getRequest().getHeaders().getFirst(CorrelationIdFilter.HEADER);
        ApiError error = ApiError.of(ErrorCode.AUTHENTICATION_REQUIRED, message, correlationId);

        byte[] bytes;
        try {
            bytes = objectMapper.writeValueAsBytes(error);
        } catch (Exception ex) {
            log.error("Failed to serialise auth error", ex);
            bytes = "{\"error\":{\"code\":\"authentication_required\"}}".getBytes(StandardCharsets.UTF_8);
        }

        DataBuffer buffer = response.bufferFactory().wrap(bytes);
        return response.writeWith(Mono.just(buffer));
    }

    @Override
    public int getOrder() {
        // After the correlation id, but well before route-level filters such as the rate
        // limiter — which needs the merchant id this filter resolves.
        return Ordered.HIGHEST_PRECEDENCE + 100;
    }

    /** Exposed for tests. */
    static List<String> spoofableHeaders() {
        return List.of(MERCHANT_HEADER, KEY_ID_HEADER, LIVEMODE_HEADER);
    }
}
