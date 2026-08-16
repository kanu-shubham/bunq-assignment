package com.paykit.gateway.filter;

import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.util.UUID;

/**
 * Mints the correlation id that every other service in the platform will log.
 *
 * <p>The gateway is the right place for this because it is the only component guaranteed to
 * see the request first. Downstream services propagate the header; they never invent one.
 *
 * <p>REACTIVE DETAIL — a {@link ServerHttpRequest} is immutable, so you do not "set a header",
 * you {@code mutate()} a decorated copy and hand that to the rest of the chain. Same idea as
 * an immutable value object: build a changed copy rather than mutating shared state.
 */
@Component
public class CorrelationIdFilter implements GlobalFilter, Ordered {

    public static final String HEADER = "X-Request-Id";

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String correlationId = exchange.getRequest().getHeaders().getFirst(HEADER);
        if (correlationId == null || correlationId.isBlank()) {
            correlationId = "req_" + UUID.randomUUID().toString().replace("-", "").substring(0, 20);
        }
        final String id = correlationId;

        ServerHttpRequest request = exchange.getRequest().mutate()
                .header(HEADER, id)
                .build();

        // Echo it back so the caller can quote it in a support ticket.
        exchange.getResponse().getHeaders().set(HEADER, id);

        return chain.filter(exchange.mutate().request(request).build());
    }

    @Override
    public int getOrder() {
        // First in the chain: an authentication failure should still be traceable.
        return Ordered.HIGHEST_PRECEDENCE;
    }
}
