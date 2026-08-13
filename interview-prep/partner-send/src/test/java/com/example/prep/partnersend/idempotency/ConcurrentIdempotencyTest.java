package com.example.prep.partnersend.idempotency;

import static org.assertj.core.api.Assertions.assertThat;

import com.example.prep.partnersend.domain.Money;
import com.example.prep.partnersend.domain.TransferRepository;
import com.example.prep.partnersend.service.TransferService;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.stream.IntStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * The test that matters. Everything else about idempotency is easy; surviving genuine
 * concurrency is the part that separates a design that works from one that only looks like
 * it does.
 *
 * <p>The scenario is real, not contrived: a partner's HTTP client times out and retries
 * while the original request is still in flight, or a load balancer replays a request to a
 * second instance. Both duplicates arrive at the same moment, on different threads, quite
 * possibly on different machines.
 *
 * <p>This is precisely where a check-then-insert implementation fails. Both threads SELECT,
 * both see nothing, both INSERT, and the customer is paid twice. No amount of application
 * locking fixes it across instances — only the database can arbitrate, which is why the
 * uniqueness lives in a primary-key constraint.
 */
@SpringBootTest
class ConcurrentIdempotencyTest {

    private static final int THREADS = 16;

    @Autowired IdempotencyService idempotency;
    @Autowired TransferService transferService;
    @Autowired TransferRepository transfers;
    @Autowired IdempotencyRepository records;

    @BeforeEach
    void clean() {
        transfers.deleteAll();
        records.deleteAll();
    }

    @Test
    @DisplayName("16 simultaneous duplicates produce exactly one transfer")
    void onlyOneWinnerUnderConcurrency() throws Exception {
        // A barrier makes all threads attempt the claim at genuinely the same moment,
        // rather than drifting apart as they start.
        CyclicBarrier startLine = new CyclicBarrier(THREADS);

        try (ExecutorService pool = Executors.newFixedThreadPool(THREADS)) {
            List<Callable<IdempotencyService.Claim>> tasks = IntStream.range(0, THREADS)
                    .<Callable<IdempotencyService.Claim>>mapToObj(i -> () -> {
                        startLine.await(10, TimeUnit.SECONDS);
                        return idempotency.claim("acme", "storm-key", "{\"amount\":25000}");
                    })
                    .toList();

            List<Future<IdempotencyService.Claim>> futures = pool.invokeAll(tasks);

            long acquired = 0;
            long rejected = 0;
            for (Future<IdempotencyService.Claim> future : futures) {
                IdempotencyService.Claim claim = future.get(10, TimeUnit.SECONDS);
                if (claim instanceof IdempotencyService.Claim.Acquired) {
                    acquired++;
                } else {
                    // Every loser is told either "in flight" or "replay" — never allowed
                    // to proceed, and never given a wrong answer.
                    assertThat(claim).isInstanceOfAny(
                            IdempotencyService.Claim.InFlight.class,
                            IdempotencyService.Claim.Replay.class);
                    rejected++;
                }
            }

            assertThat(acquired).as("exactly one thread may own the key").isEqualTo(1);
            assertThat(rejected).isEqualTo(THREADS - 1);
            assertThat(records.count()).isEqualTo(1);
        }
    }

    @Test
    @DisplayName("only the winning thread's transfer is ever created")
    void concurrentDuplicatesCreateOneTransfer() throws Exception {
        CyclicBarrier startLine = new CyclicBarrier(THREADS);

        try (ExecutorService pool = Executors.newFixedThreadPool(THREADS)) {
            List<Callable<Boolean>> tasks = IntStream.range(0, THREADS)
                    .<Callable<Boolean>>mapToObj(i -> () -> {
                        startLine.await(10, TimeUnit.SECONDS);
                        var claim = idempotency.claim("acme", "pay-once", "{\"amount\":25000}");
                        if (claim instanceof IdempotencyService.Claim.Acquired acquired) {
                            transferService.acceptTransfer("acme", "REF-1", Money.of("GBP", 25_000));
                            idempotency.complete(acquired.id(), 202, "{\"status\":\"RECEIVED\"}");
                            return true;
                        }
                        return false;
                    })
                    .toList();

            long winners = pool.invokeAll(tasks).stream()
                    .map(f -> {
                        try {
                            return f.get(10, TimeUnit.SECONDS);
                        } catch (Exception e) {
                            throw new AssertionError(e);
                        }
                    })
                    .filter(Boolean::booleanValue)
                    .count();

            assertThat(winners).isEqualTo(1);
            assertThat(transfers.count()).as("the money moves once").isEqualTo(1);
        }
    }

    @Test
    @DisplayName("a released claim lets the client's retry through")
    void releaseUnlocksTheKey() {
        var first = idempotency.claim("acme", "retry-me", "{}");
        assertThat(first).isInstanceOf(IdempotencyService.Claim.Acquired.class);

        // Simulates the work failing: the transaction rolled back, so nothing happened
        // and the key must not stay locked.
        idempotency.release(((IdempotencyService.Claim.Acquired) first).id());

        assertThat(idempotency.claim("acme", "retry-me", "{}"))
                .isInstanceOf(IdempotencyService.Claim.Acquired.class);
    }
}
