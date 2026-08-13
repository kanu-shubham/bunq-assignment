package com.example.prep.partnersend.outbox;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.example.prep.partnersend.domain.Money;
import com.example.prep.partnersend.domain.TransferRepository;
import com.example.prep.partnersend.service.TransferService;
import java.time.Clock;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Proves the two properties the outbox pattern exists to provide:
 * <b>atomicity</b> with the business write, and <b>at-least-once</b> delivery afterwards.
 */
@SpringBootTest
class OutboxTest {

    /** Records what was published and can be told to fail, without needing a broker. */
    static class RecordingPublisher implements EventPublisher {
        final List<String> published = new ArrayList<>();
        final AtomicBoolean failNext = new AtomicBoolean(false);

        @Override
        public void publish(OutboxEvent event) {
            if (failNext.get()) {
                throw new IllegalStateException("broker unavailable");
            }
            published.add(event.getEventId());
        }
    }

    @TestConfiguration
    static class Config {
        /** {@code @Primary} so the app's own OutboxPublisher bean gets this one rather
         *  than the logging stand-in, with no ambiguity to resolve. */
        @Bean
        @Primary
        RecordingPublisher recordingPublisher() {
            return new RecordingPublisher();
        }
    }

    @Autowired TransferService transferService;
    @Autowired TransferRepository transfers;
    @Autowired OutboxRepository outbox;
    @Autowired RecordingPublisher publisher;
    @Autowired Clock clock;
    @Autowired TransactionTemplate transactionTemplate;

    @BeforeEach
    void clean() {
        outbox.deleteAll();
        transfers.deleteAll();
        publisher.published.clear();
        publisher.failNext.set(false);
    }

    private OutboxPublisher newPublisher() {
        return new OutboxPublisher(outbox, publisher, clock, 100);
    }

    @Test
    @DisplayName("the transfer and its event are written in one transaction")
    void businessWriteAndEventAreAtomic() {
        var transfer = transferService.acceptTransfer("acme", "REF-1", Money.of("GBP", 25_000));

        assertThat(transfers.count()).isEqualTo(1);
        var events = outbox.findByAggregateIdOrderBySequenceNoAsc(transfer.getId());
        assertThat(events).hasSize(1);
        assertThat(events.get(0).getEventType()).isEqualTo("transfer.received");
        assertThat(events.get(0).isPublished()).isFalse();
        assertThat(events.get(0).getPayload()).contains(transfer.getId(), "25000", "GBP");
    }

    @Test
    @DisplayName("if the transaction rolls back, neither the transfer nor the event survives")
    void rollbackLosesBoth() {
        assertThatThrownBy(() -> transactionTemplate.execute(status -> {
            transferService.acceptTransfer("acme", "REF-DOOMED", Money.of("GBP", 25_000));
            // Something later in the same unit of work explodes — a limit check, a
            // sanctions hit, a bug. This is the case the dual-write problem gets wrong:
            // with a direct kafka.send() the event would already be gone and irretrievable.
            throw new IllegalStateException("sanctions check failed");
        })).isInstanceOf(IllegalStateException.class);

        assertThat(transfers.count()).isZero();
        assertThat(outbox.count()).as("no event for a transfer that does not exist").isZero();
    }

    @Test
    void drainPublishesAndMarksSent() {
        transferService.acceptTransfer("acme", "REF-2", Money.of("GBP", 100));

        assertThat(newPublisher().drainOnce()).isEqualTo(1);
        assertThat(publisher.published).hasSize(1);
        assertThat(outbox.findUnpublished()).isEmpty();

        // A second drain must not re-publish what is already marked sent.
        assertThat(newPublisher().drainOnce()).isZero();
        assertThat(publisher.published).hasSize(1);
    }

    @Test
    @DisplayName("a broker failure leaves the event pending and it goes out on the next poll")
    void failedPublishIsRetried() {
        transferService.acceptTransfer("acme", "REF-3", Money.of("GBP", 100));

        publisher.failNext.set(true);
        assertThat(newPublisher().drainOnce()).isZero();
        assertThat(publisher.published).isEmpty();
        assertThat(outbox.findUnpublished()).hasSize(1);
        assertThat(outbox.findUnpublished().get(0).getAttempts()).isEqualTo(1);

        publisher.failNext.set(false);
        assertThat(newPublisher().drainOnce()).isEqualTo(1);
        assertThat(publisher.published).hasSize(1);
    }

    @Test
    @DisplayName("events are drained oldest-first so per-transfer ordering survives")
    void preservesOrdering() {
        transferService.acceptTransfer("acme", "REF-A", Money.of("GBP", 1));
        transferService.acceptTransfer("acme", "REF-B", Money.of("GBP", 2));
        transferService.acceptTransfer("acme", "REF-C", Money.of("GBP", 3));

        var pending = outbox.findUnpublished();
        assertThat(pending).hasSize(3);
        assertThat(pending)
                .extracting(OutboxEvent::getSequenceNo)
                .isSorted();

        newPublisher().drainOnce();
        assertThat(publisher.published).hasSize(3);
    }

    @Test
    @DisplayName("at-least-once is the honest guarantee, so events carry a dedup id")
    void eventsCarryAStableIdForConsumerDeduplication() {
        var transfer = transferService.acceptTransfer("acme", "REF-4", Money.of("GBP", 100));
        var event = outbox.findByAggregateIdOrderBySequenceNoAsc(transfer.getId()).get(0);

        // The poller can publish and die before marking the row sent, so consumers will
        // see this id twice. That is expected, and it is why the id exists.
        assertThat(event.getEventId()).isNotBlank();
        assertThat(event.getAggregateId()).isEqualTo(transfer.getId());
    }
}
