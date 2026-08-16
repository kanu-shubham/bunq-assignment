package com.paykit.webhook;

import com.paykit.webhook.domain.WebhookDelivery;
import com.paykit.webhook.domain.WebhookEndpoint;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.time.Duration;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

class WebhookDeliveryTest {

    private static WebhookDelivery newDelivery(int maxAttempts) {
        return new WebhookDelivery("whd_1", "we_1", "acct_1", "evt_1",
                "payment_intent.succeeded", "{}", maxAttempts);
    }

    @Test
    void startsPendingAndDueImmediately() {
        WebhookDelivery delivery = newDelivery(8);

        assertThat(delivery.getStatus()).isEqualTo(WebhookDelivery.Status.PENDING);
        assertThat(delivery.getAttempts()).isZero();
        assertThat(delivery.getNextAttemptAt()).isNotNull();
    }

    @Test
    void successIsTerminalAndStopsRetrying() {
        WebhookDelivery delivery = newDelivery(8);
        delivery.markSucceeded(200);

        assertThat(delivery.getStatus()).isEqualTo(WebhookDelivery.Status.SUCCEEDED);
        assertThat(delivery.getDeliveredAt()).isNotNull();
        assertThat(delivery.getNextAttemptAt()).isNull();
        assertThat(delivery.getAttempts()).isEqualTo(1);
    }

    @Test
    @DisplayName("failures are retried until max attempts, then parked as FAILED")
    void givesUpAfterMaxAttempts() {
        WebhookDelivery delivery = newDelivery(3);

        delivery.markAttemptFailed(500, "boom", 0.5);
        assertThat(delivery.getStatus()).isEqualTo(WebhookDelivery.Status.PENDING);
        assertThat(delivery.getNextAttemptAt()).isNotNull();

        delivery.markAttemptFailed(500, "boom", 0.5);
        assertThat(delivery.getStatus()).isEqualTo(WebhookDelivery.Status.PENDING);

        delivery.markAttemptFailed(500, "boom", 0.5);
        assertThat(delivery.getStatus()).isEqualTo(WebhookDelivery.Status.FAILED);
        // Nothing left to schedule — a FAILED delivery waits for a manual replay.
        assertThat(delivery.getNextAttemptAt()).isNull();
        assertThat(delivery.getAttempts()).isEqualTo(3);
    }

    @Test
    @DisplayName("backoff grows exponentially and is capped at an hour")
    void backoffGrowsAndIsCapped() {
        // jitterFactor 0.5 => exactly the nominal delay, no randomisation.
        assertThat(WebhookDelivery.backoffFor(1, 0.5)).isEqualTo(Duration.ofSeconds(10));
        assertThat(WebhookDelivery.backoffFor(2, 0.5)).isEqualTo(Duration.ofSeconds(20));
        assertThat(WebhookDelivery.backoffFor(3, 0.5)).isEqualTo(Duration.ofSeconds(40));
        assertThat(WebhookDelivery.backoffFor(8, 0.5)).isEqualTo(Duration.ofSeconds(1280));

        // Doubling forever would reach days between attempts, which helps nobody.
        assertThat(WebhookDelivery.backoffFor(20, 0.5)).isEqualTo(Duration.ofHours(1));
    }

    @ParameterizedTest
    @ValueSource(doubles = {0.0, 0.25, 0.5, 0.75, 1.0})
    @DisplayName("jitter spreads retries across ±50% so a recovering server is not stampeded")
    void jitterStaysWithinBounds(double jitter) {
        Duration nominal = Duration.ofSeconds(40);        // attempt 3
        Duration actual = WebhookDelivery.backoffFor(3, jitter);

        assertThat(actual).isBetween(
                Duration.ofMillis((long) (nominal.toMillis() * 0.5)),
                Duration.ofMillis((long) (nominal.toMillis() * 1.5)));
    }

    @Test
    void replayResetsTheAttemptCounter() {
        WebhookDelivery delivery = newDelivery(2);
        delivery.markAttemptFailed(500, "boom", 0.5);
        delivery.markAttemptFailed(500, "boom", 0.5);
        assertThat(delivery.getStatus()).isEqualTo(WebhookDelivery.Status.FAILED);

        delivery.resetForReplay();

        assertThat(delivery.getStatus()).isEqualTo(WebhookDelivery.Status.PENDING);
        assertThat(delivery.getAttempts()).isZero();
        assertThat(delivery.getLastError()).isNull();
    }

    @Test
    void errorMessagesAreTruncatedSoTheyCannotBreakTheInsert() {
        WebhookDelivery delivery = newDelivery(8);
        delivery.markAttemptFailed(500, "x".repeat(2_000), 0.5);

        assertThat(delivery.getLastError()).hasSize(500);
    }

    @Test
    @DisplayName("an endpoint subscribes to specific events, or to everything with *")
    void endpointEventFiltering() {
        WebhookEndpoint specific = new WebhookEndpoint("we_1", "acct_1", "https://example.com/hook",
                "whsec_1", Set.of("payment_intent.succeeded"), "orders");

        assertThat(specific.wants("payment_intent.succeeded")).isTrue();
        assertThat(specific.wants("refund.succeeded")).isFalse();

        WebhookEndpoint wildcard = new WebhookEndpoint("we_2", "acct_1", "https://example.com/all",
                "whsec_2", Set.of(), "everything");

        assertThat(wildcard.wants("payment_intent.succeeded")).isTrue();
        assertThat(wildcard.wants("anything.at.all")).isTrue();
    }

    @Test
    @DisplayName("an endpoint that keeps failing is auto-disabled and stops receiving events")
    void endpointIsDisabledAfterRepeatedFailures() {
        WebhookEndpoint endpoint = new WebhookEndpoint("we_3", "acct_1", "https://dead.example.com",
                "whsec_3", Set.of("*"), "abandoned server");

        for (int i = 0; i < 4; i++) {
            endpoint.recordFailure(5);
            assertThat(endpoint.getStatus()).isEqualTo(WebhookEndpoint.Status.ENABLED);
        }
        endpoint.recordFailure(5);

        assertThat(endpoint.getStatus()).isEqualTo(WebhookEndpoint.Status.DISABLED);
        assertThat(endpoint.wants("payment_intent.succeeded")).isFalse();

        endpoint.enable();
        assertThat(endpoint.getStatus()).isEqualTo(WebhookEndpoint.Status.ENABLED);
        assertThat(endpoint.getConsecutiveFailures()).isZero();
    }

    @Test
    void aSingleSuccessClearsTheFailureStreak() {
        WebhookEndpoint endpoint = new WebhookEndpoint("we_4", "acct_1", "https://flaky.example.com",
                "whsec_4", Set.of("*"), "flaky");

        endpoint.recordFailure(50);
        endpoint.recordFailure(50);
        endpoint.recordSuccess();

        assertThat(endpoint.getConsecutiveFailures()).isZero();
    }
}
