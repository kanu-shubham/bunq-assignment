package com.example.prep.partnersend.domain;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TransferRepository extends JpaRepository<Transfer, String> {

    Optional<Transfer> findByPartnerIdAndPartnerReference(String partnerId, String partnerReference);
}
