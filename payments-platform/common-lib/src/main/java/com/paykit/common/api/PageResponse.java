package com.paykit.common.api;

import java.util.List;
import java.util.function.Function;

/**
 * A list page in the Stripe shape: {@code { "object": "list", "data": [...], "has_more": true }}.
 *
 * <p>JAVA CONCEPT — a generic record plus a {@code Function<T, R>} mapper. {@link #map} converts
 * a page of JPA entities into a page of DTOs without the caller writing a loop, and without
 * {@code PageResponse} knowing anything about either type.
 */
public record PageResponse<T>(String object, List<T> data, boolean hasMore, int limit, String nextCursor) {

    public static <T> PageResponse<T> of(List<T> data, boolean hasMore, int limit, String nextCursor) {
        return new PageResponse<>("list", data, hasMore, limit, nextCursor);
    }

    public <R> PageResponse<R> map(Function<? super T, ? extends R> mapper) {
        return new PageResponse<>(object, data.stream().<R>map(mapper::apply).toList(), hasMore, limit, nextCursor);
    }
}
