package com.paykit.webhook.repository;

import com.paykit.webhook.domain.ProcessedEvent;
import com.paykit.webhook.domain.WebhookDelivery;
import com.paykit.webhook.domain.WebhookEndpoint;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public final class WebhookRepositories {

    private WebhookRepositories() {
        throw new AssertionError("No instances");
    }

    public interface WebhookEndpointRepository extends JpaRepository<WebhookEndpoint, String> {

        List<WebhookEndpoint> findByMerchantId(String merchantId);

        List<WebhookEndpoint> findByMerchantIdAndStatus(String merchantId, WebhookEndpoint.Status status);

        Optional<WebhookEndpoint> findByIdAndMerchantId(String id, String merchantId);
    }

    public interface WebhookDeliveryRepository extends JpaRepository<WebhookDelivery, String> {

        /**
         * Claims a batch of deliveries that are due.
         *
         * <p>{@code FOR UPDATE SKIP LOCKED} again: several webhook-service instances poll this
         * table concurrently and each takes a disjoint batch, so throughput scales with
         * replicas and no delivery is sent twice by two pods at once.
         */
        @Query(value = """
                SELECT * FROM webhook_deliveries
                WHERE status = 'PENDING' AND next_attempt_at <= :now
                ORDER BY next_attempt_at
                LIMIT :batchSize
                FOR UPDATE SKIP LOCKED
                """, nativeQuery = true)
        List<WebhookDelivery> claimDue(@Param("now") Instant now, @Param("batchSize") int batchSize);

        Page<WebhookDelivery> findByMerchantIdOrderByCreatedAtDesc(String merchantId, Pageable pageable);

        Optional<WebhookDelivery> findByIdAndMerchantId(String id, String merchantId);

        boolean existsByEventIdAndEndpointId(String eventId, String endpointId);

        long countByStatus(WebhookDelivery.Status status);
    }

    public interface ProcessedEventRepository extends JpaRepository<ProcessedEvent, String> {
    }
}
