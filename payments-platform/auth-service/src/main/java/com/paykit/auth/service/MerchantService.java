package com.paykit.auth.service;

import com.paykit.auth.domain.Merchant;
import com.paykit.auth.repository.MerchantRepository;
import com.paykit.common.error.Exceptions;
import com.paykit.common.util.Ids;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Set;

@Service
public class MerchantService {

    private static final Logger log = LoggerFactory.getLogger(MerchantService.class);

    private final MerchantRepository merchantRepository;
    private final ApiKeyService apiKeyService;

    public MerchantService(MerchantRepository merchantRepository, ApiKeyService apiKeyService) {
        this.merchantRepository = merchantRepository;
        this.apiKeyService = apiKeyService;
    }

    /**
     * Onboards a merchant and hands back their first test key.
     *
     * <p>Both writes share one transaction: a merchant with no key would be useless, and a key
     * with no merchant is a foreign-key violation. Atomicity is not a nicety here — it is the
     * difference between a working account and a support ticket.
     */
    @Transactional
    public Onboarding register(String name, String email, String countryCode) {
        if (merchantRepository.existsByEmail(email)) {
            throw new Exceptions.InvalidRequestException(
                    "A merchant with this email already exists", "email");
        }

        Merchant merchant = new Merchant(Ids.generate(Ids.MERCHANT), name, email, countryCode);
        // Auto-activated for the demo. A real platform runs KYC/AML checks first and leaves
        // the account PENDING until a human or an automated check clears it.
        merchant.activate();
        merchantRepository.save(merchant);

        GeneratedApiKey key = apiKeyService.issue(
                merchant, false, Set.of("payments:read", "payments:write"));

        log.info("Registered merchant {} ({})", merchant.getId(), email);
        return new Onboarding(merchant, key);
    }

    /** {@code readOnly = true} lets the driver and Hibernate skip dirty checking and flushing. */
    @Transactional(readOnly = true)
    public Merchant require(String merchantId) {
        return merchantRepository.findById(merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("merchant", merchantId));
    }

    @Transactional
    public Merchant suspend(String merchantId, String reason) {
        Merchant merchant = require(merchantId);
        merchant.suspend(reason);
        // No explicit save(): inside a transaction the entity is *managed*, so Hibernate
        // detects the change and flushes an UPDATE at commit. Forgetting this is why people
        // think their setters "did not persist" outside a transaction.
        return merchant;
    }

    public record Onboarding(Merchant merchant, GeneratedApiKey apiKey) {
    }
}
