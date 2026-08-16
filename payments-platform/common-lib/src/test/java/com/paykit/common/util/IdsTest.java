package com.paykit.common.util;

import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;

class IdsTest {

    @Test
    void producesPrefixedIds() {
        String id = Ids.generate(Ids.PAYMENT_INTENT);

        assertThat(id).startsWith("pi_").hasSize(23);
        assertThat(Ids.hasPrefix(id, Ids.PAYMENT_INTENT)).isTrue();
        assertThat(Ids.hasPrefix(id, Ids.CHARGE)).isFalse();
    }

    /**
     * CONCURRENCY — ids are minted on every request thread at once. This asserts that
     * {@code SecureRandom} really is safe to share and that we get no collisions.
     * Virtual threads (Java 21) make spinning up 500 concurrent tasks essentially free.
     */
    @Test
    void isCollisionFreeUnderConcurrency() throws Exception {
        int tasks = 500;
        try (ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor()) {
            List<Future<String>> futures = new java.util.ArrayList<>();
            IntStream.range(0, tasks)
                    .forEach(i -> futures.add(executor.submit(() -> Ids.generate(Ids.CHARGE))));

            Set<String> ids = new HashSet<>();
            for (Future<String> future : futures) {
                ids.add(future.get());
            }
            assertThat(ids).hasSize(tasks);
        }
    }
}
