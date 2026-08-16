package com.paykit.auth.repository;

import com.paykit.auth.domain.ApiKey;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.util.List;
import java.util.Optional;

public interface ApiKeyRepository extends JpaRepository<ApiKey, String> {

    /**
     * {@code JOIN FETCH} loads the key and its merchant in <b>one</b> query.
     *
     * <p>Without it, the lazy {@code merchant} association triggers a second SELECT the moment
     * {@code key.getMerchant().canAcceptPayments()} is called — and if the session has already
     * closed, a LazyInitializationException instead. This is the N+1 problem solved explicitly
     * at the query that needs it, rather than by making the mapping EAGER and paying the cost
     * everywhere.
     */
    @Query("SELECT k FROM ApiKey k JOIN FETCH k.merchant WHERE k.keyPrefix = :prefix")
    Optional<ApiKey> findByKeyPrefixWithMerchant(String prefix);

    List<ApiKey> findByMerchantIdOrderByCreatedAtDesc(String merchantId);
}
