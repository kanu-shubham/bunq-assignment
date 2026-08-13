package com.example.prep.partnersend.domain;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class TransferStateMachineTest {

    private static final Clock CLOCK = Clock.fixed(Instant.parse("2026-01-01T00:00:00Z"), ZoneOffset.UTC);

    private Transfer newTransfer() {
        return Transfer.receive("partner-1", "ref-1", Money.of("GBP", 10_000), CLOCK.instant());
    }

    @Test
    void happyPath() {
        Transfer t = newTransfer();
        assertThat(t.getStatus()).isEqualTo(TransferStatus.RECEIVED);

        t.transitionTo(TransferStatus.VALIDATED, CLOCK.instant());
        t.transitionTo(TransferStatus.FUNDED, CLOCK.instant());
        t.markSubmitted("SCHEME-1", CLOCK.instant());
        t.transitionTo(TransferStatus.SETTLED, CLOCK.instant());

        assertThat(t.getStatus()).isEqualTo(TransferStatus.SETTLED);
        assertThat(t.getSchemeReference()).isEqualTo("SCHEME-1");
    }

    @Test
    @DisplayName("a late duplicate webhook cannot skip states")
    void rejectsIllegalTransition() {
        Transfer t = newTransfer();
        assertThatThrownBy(() -> t.transitionTo(TransferStatus.SETTLED, CLOCK.instant()))
                .isInstanceOf(IllegalTransitionException.class)
                .hasMessageContaining("RECEIVED -> SETTLED");

        // The failed attempt left the aggregate untouched.
        assertThat(t.getStatus()).isEqualTo(TransferStatus.RECEIVED);
    }

    @Test
    @DisplayName("settled is not the end: funds can still be returned")
    void settledCanBeReturned() {
        Transfer t = newTransfer();
        t.transitionTo(TransferStatus.VALIDATED, CLOCK.instant());
        t.transitionTo(TransferStatus.FUNDED, CLOCK.instant());
        t.markSubmitted("SCHEME-1", CLOCK.instant());
        t.transitionTo(TransferStatus.SETTLED, CLOCK.instant());

        assertThat(TransferStatus.SETTLED.isTerminal()).isFalse();
        t.transitionTo(TransferStatus.RETURNED, CLOCK.instant());
        assertThat(t.getStatus()).isEqualTo(TransferStatus.RETURNED);
        assertThat(TransferStatus.RETURNED.isTerminal()).isTrue();
    }

    @Test
    void terminalStatesHaveNoWayOut() {
        assertThat(TransferStatus.FAILED.isTerminal()).isTrue();
        assertThat(TransferStatus.RETURNED.isTerminal()).isTrue();
        assertThat(TransferStatus.RECEIVED.isTerminal()).isFalse();
    }

    @Test
    void amountMustBePositive() {
        assertThatThrownBy(() -> Transfer.receive("p", "r", Money.of("GBP", 0), CLOCK.instant()))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> Transfer.receive("p", "r", Money.of("GBP", -1), CLOCK.instant()))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
