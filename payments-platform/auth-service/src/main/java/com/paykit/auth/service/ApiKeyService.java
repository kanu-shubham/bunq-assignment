package com.paykit.auth.service;

import com.paykit.auth.domain.ApiKey;
import com.paykit.auth.domain.Merchant;
import com.paykit.auth.repository.ApiKeyRepository;
import com.paykit.common.error.Exceptions;
import com.paykit.common.util.Ids;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Mints and verifies API keys.
 *
 * <p>SPRING CONCEPT — constructor injection. The dependencies are {@code final} and supplied
 * by the container, which means the class cannot exist in a half-built state and can be
 * constructed directly in a unit test with fakes. Field injection ({@code @Autowired} on a
 * field) gives up both of those properties; prefer constructors, always.
 */
@Service
public class ApiKeyService {

    private static final Logger log = LoggerFactory.getLogger(ApiKeyService.class);
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final char[] ALPHABET =
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".toCharArray();
    private static final int PREFIX_RANDOM_LENGTH = 8;
    private static final int SECRET_LENGTH = 24;
    private static final Set<String> DEFAULT_SCOPES = Set.of("payments:read", "payments:write");

    private final ApiKeyRepository apiKeyRepository;
    private final PasswordEncoder passwordEncoder;

    public ApiKeyService(ApiKeyRepository apiKeyRepository, PasswordEncoder passwordEncoder) {
        this.apiKeyRepository = apiKeyRepository;
        this.passwordEncoder = passwordEncoder;
    }

    /**
     * Issues a key of the form {@code sk_test_<prefix>_<secret>}.
     *
     * <p>{@code @Transactional} opens a transaction around the method and commits when it
     * returns normally — or rolls back if it throws an unchecked exception. Note the default:
     * a <em>checked</em> exception would commit, which surprises people regularly.
     */
    @Transactional
    public GeneratedApiKey issue(Merchant merchant, boolean livemode, Set<String> scopes) {
        String environment = livemode ? "live" : "test";
        String prefix = "sk_%s_%s".formatted(environment, randomString(PREFIX_RANDOM_LENGTH));
        String secret = randomString(SECRET_LENGTH);
        String plaintext = prefix + "_" + secret;

        ApiKey key = new ApiKey(
                Ids.generate(Ids.API_KEY),
                merchant,
                prefix,
                passwordEncoder.encode(secret),
                livemode,
                scopes == null || scopes.isEmpty() ? DEFAULT_SCOPES : scopes);

        apiKeyRepository.save(key);
        // Log the prefix, never the secret. The prefix is enough to trace a key through
        // the logs and to revoke it; the secret is enough to spend the merchant's money.
        log.info("Issued API key {} ({}) for merchant {}", key.getId(), prefix, merchant.getId());

        return new GeneratedApiKey(key, plaintext);
    }

    /**
     * Verifies a presented key. Returns empty for <em>every</em> failure mode — unknown
     * prefix, wrong secret, revoked key, suspended merchant — so that the caller cannot
     * distinguish "no such key" from "wrong secret" and use the API as an oracle.
     */
    @Transactional
    public Optional<ApiKey> verify(String plaintext) {
        String[] parts = splitKey(plaintext);
        if (parts == null) {
            return Optional.empty();
        }
        String prefix = parts[0];
        String secret = parts[1];

        Optional<ApiKey> found = apiKeyRepository.findByKeyPrefixWithMerchant(prefix);
        if (found.isEmpty()) {
            // Spend the bcrypt time anyway. Returning instantly for an unknown prefix but
            // slowly for a known one leaks which prefixes exist — a timing side channel.
            passwordEncoder.encode(secret);
            return Optional.empty();
        }

        ApiKey key = found.get();
        if (!passwordEncoder.matches(secret, key.getSecretHash()) || !key.isUsable()) {
            return Optional.empty();
        }

        key.markUsed();
        return Optional.of(key);
    }

    @Transactional(readOnly = true)
    public List<ApiKey> listForMerchant(String merchantId) {
        return apiKeyRepository.findByMerchantIdOrderByCreatedAtDesc(merchantId);
    }

    @Transactional
    public void revoke(String merchantId, String keyId) {
        ApiKey key = apiKeyRepository.findById(keyId)
                .orElseThrow(() -> new Exceptions.NotFoundException("api key", keyId));

        // Authorisation, not just authentication: a valid caller must still not be able to
        // revoke a key belonging to someone else. Checking ownership on every lookup is the
        // difference between multi-tenant and multi-tenant-with-a-data-breach.
        if (!key.getMerchant().getId().equals(merchantId)) {
            throw new Exceptions.NotFoundException("api key", keyId);
        }
        key.revoke();
        log.info("Revoked API key {} for merchant {}", keyId, merchantId);
    }

    /** {@code sk_test_ABCD1234_secret...} -> {@code ["sk_test_ABCD1234", "secret..."]} */
    private static String[] splitKey(String plaintext) {
        if (plaintext == null || plaintext.isBlank()) {
            return null;
        }
        int lastUnderscore = plaintext.lastIndexOf('_');
        if (lastUnderscore <= 0 || lastUnderscore == plaintext.length() - 1) {
            return null;
        }
        return new String[]{plaintext.substring(0, lastUnderscore), plaintext.substring(lastUnderscore + 1)};
    }

    private static String randomString(int length) {
        StringBuilder sb = new StringBuilder(length);
        for (int i = 0; i < length; i++) {
            sb.append(ALPHABET[RANDOM.nextInt(ALPHABET.length)]);
        }
        return sb.toString();
    }
}
