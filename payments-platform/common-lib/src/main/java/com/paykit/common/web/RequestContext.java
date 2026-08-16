package com.paykit.common.web;

/**
 * Per-request identifiers made available to any layer without threading them through
 * every method signature.
 *
 * <p>JAVA CONCEPT — {@link ThreadLocal}. Each thread sees its own value, so a servlet
 * container serving 200 concurrent requests keeps 200 independent contexts.
 *
 * <p>THE TRAP — a thread pool <em>reuses</em> threads. If the value is not removed at the end
 * of the request, the next request on that thread inherits it: cross-request data leakage and,
 * in a container, a classloader leak. {@link #clear()} in a {@code finally} block is mandatory;
 * {@link CorrelationIdFilter} does exactly that.
 */
public final class RequestContext {

    private static final ThreadLocal<String> CORRELATION_ID = new ThreadLocal<>();
    private static final ThreadLocal<String> MERCHANT_ID = new ThreadLocal<>();

    private RequestContext() {
        throw new AssertionError("No instances");
    }

    public static void setCorrelationId(String value) {
        CORRELATION_ID.set(value);
    }

    public static String correlationId() {
        return CORRELATION_ID.get();
    }

    public static void setMerchantId(String value) {
        MERCHANT_ID.set(value);
    }

    public static String merchantId() {
        return MERCHANT_ID.get();
    }

    /** Always call this in a {@code finally} block. */
    public static void clear() {
        CORRELATION_ID.remove();
        MERCHANT_ID.remove();
    }
}
