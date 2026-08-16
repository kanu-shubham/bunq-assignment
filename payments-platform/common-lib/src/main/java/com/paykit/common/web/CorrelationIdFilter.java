package com.paykit.common.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;

/**
 * Stamps every request with a correlation id and puts it in the logging MDC.
 *
 * <p>WHY IT MATTERS — one {@code POST /v1/payment_intents} touches nginx, the gateway,
 * payment-service, Kafka, the ledger and the webhook sender. Without a shared id you are
 * grepping six services by timestamp and guessing. With one, {@code grep req_abc123} tells
 * the whole story. The gateway generates the id; every downstream service propagates it.
 *
 * <p>SPRING CONCEPT — {@link OncePerRequestFilter} guarantees a single execution per request
 * even when the request is dispatched again internally (forward, async, error page).
 * {@code @Order(HIGHEST_PRECEDENCE)} puts it ahead of security so authentication failures
 * are logged with an id too.
 */
@Order(Ordered.HIGHEST_PRECEDENCE)
public class CorrelationIdFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Request-Id";
    public static final String MDC_KEY = "correlationId";
    private static final String MDC_MERCHANT_KEY = "merchantId";

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String correlationId = request.getHeader(HEADER);
        if (correlationId == null || correlationId.isBlank()) {
            correlationId = "req_" + UUID.randomUUID().toString().replace("-", "").substring(0, 20);
        }

        RequestContext.setCorrelationId(correlationId);
        MDC.put(MDC_KEY, correlationId);
        response.setHeader(HEADER, correlationId);

        try {
            chain.doFilter(request, response);
        } finally {
            // Mandatory: this thread goes straight back into the pool.
            MDC.remove(MDC_KEY);
            MDC.remove(MDC_MERCHANT_KEY);
            RequestContext.clear();
        }
    }
}
