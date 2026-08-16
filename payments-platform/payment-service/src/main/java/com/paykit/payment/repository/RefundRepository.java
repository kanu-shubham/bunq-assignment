package com.paykit.payment.repository;

import com.paykit.payment.domain.Refund;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface RefundRepository extends JpaRepository<Refund, String> {

    Optional<Refund> findByIdAndMerchantId(String id, String merchantId);

    List<Refund> findByChargeIdOrderByCreatedAtDesc(String chargeId);

    Page<Refund> findByMerchantIdOrderByCreatedAtDesc(String merchantId, Pageable pageable);
}
