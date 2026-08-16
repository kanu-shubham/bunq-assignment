package com.paykit.payment.repository;

import com.paykit.payment.domain.Charge;
import jakarta.persistence.LockModeType;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

import java.util.Optional;

public interface ChargeRepository extends JpaRepository<Charge, String> {

    Optional<Charge> findByIdAndMerchantId(String id, String merchantId);

    Optional<Charge> findByPaymentIntentId(String paymentIntentId);

    Page<Charge> findByMerchantIdOrderByCreatedAtDesc(String merchantId, Pageable pageable);

    /**
     * Refunds mutate {@code refundedMinor}, so two concurrent partial refunds could each read
     * "0 refunded" and both succeed, refunding more than was captured. The row lock makes the
     * read-check-write sequence atomic.
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT c FROM Charge c WHERE c.id = :id AND c.merchantId = :merchantId")
    Optional<Charge> findForUpdate(String id, String merchantId);
}
