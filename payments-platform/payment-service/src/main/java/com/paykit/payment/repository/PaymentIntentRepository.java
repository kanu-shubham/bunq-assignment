package com.paykit.payment.repository;

import com.paykit.payment.domain.PaymentIntent;
import com.paykit.payment.domain.PaymentIntentStatus;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

import jakarta.persistence.LockModeType;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface PaymentIntentRepository extends JpaRepository<PaymentIntent, String> {

    /**
     * Always look up by id <em>and</em> merchant. Fetching by id alone and checking ownership
     * afterwards works right up until someone forgets the check; making the tenant part of the
     * query means the mistake cannot be made.
     */
    Optional<PaymentIntent> findByIdAndMerchantId(String id, String merchantId);

    Page<PaymentIntent> findByMerchantIdOrderByCreatedAtDesc(String merchantId, Pageable pageable);

    Page<PaymentIntent> findByMerchantIdAndStatusOrderByCreatedAtDesc(
            String merchantId, PaymentIntentStatus status, Pageable pageable);

    /**
     * PESSIMISTIC_WRITE issues {@code SELECT ... FOR UPDATE}: the row is locked until the
     * transaction ends, so a second confirm blocks instead of racing.
     *
     * <p>OPTIMISTIC (@Version) vs PESSIMISTIC — optimistic assumes conflicts are rare and
     * detects them at commit; it scales better and is the default everywhere else here.
     * Pessimistic serialises upfront and is worth it exactly where a lost race means calling
     * the card network twice. Confirming a payment is that place.
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT p FROM PaymentIntent p WHERE p.id = :id AND p.merchantId = :merchantId")
    Optional<PaymentIntent> findForUpdate(String id, String merchantId);

    /**
     * Payments stuck in PROCESSING: the acquirer call started but never finished, e.g. the
     * pod was killed mid-request. A reconciliation job sweeps these — an unattended payment
     * in a non-terminal state is money in limbo.
     */
    @Query("""
            SELECT p FROM PaymentIntent p
            WHERE p.status = com.paykit.payment.domain.PaymentIntentStatus.PROCESSING
              AND p.confirmedAt < :threshold
            """)
    List<PaymentIntent> findStuckInProcessing(Instant threshold);

    long countByMerchantIdAndStatus(String merchantId, PaymentIntentStatus status);
}
