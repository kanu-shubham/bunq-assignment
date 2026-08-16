package com.paykit.auth.repository;

import com.paykit.auth.domain.Merchant;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

/**
 * SPRING DATA CONCEPT — you declare an interface; Spring generates the implementation at
 * startup. {@code findByEmail} is parsed from the method <em>name</em> into
 * {@code SELECT ... WHERE email = ?}. No SQL, no DAO boilerplate, and the query is validated
 * against the entity at startup rather than failing on first call.
 */
public interface MerchantRepository extends JpaRepository<Merchant, String> {

    Optional<Merchant> findByEmail(String email);

    boolean existsByEmail(String email);
}
