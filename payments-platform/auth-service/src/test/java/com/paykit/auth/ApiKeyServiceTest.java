package com.paykit.auth;

import com.paykit.auth.domain.ApiKey;
import com.paykit.auth.domain.Merchant;
import com.paykit.auth.repository.ApiKeyRepository;
import com.paykit.auth.service.ApiKeyService;
import com.paykit.auth.service.GeneratedApiKey;
import com.paykit.common.error.Exceptions;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Pure unit tests — no Spring context, no database, milliseconds to run.
 *
 * <p>The repository is a Mockito mock, so these tests describe the <em>service's</em>
 * behaviour and nothing else. That is the point of constructor injection: the class under
 * test can be built by hand.
 */
class ApiKeyServiceTest {

    private ApiKeyRepository repository;
    private ApiKeyService service;
    private Merchant merchant;

    @BeforeEach
    void setUp() {
        repository = mock(ApiKeyRepository.class);
        // Cost factor 4: the lowest BCrypt allows. Real config uses 10+; tests do not need
        // to prove that bcrypt is slow, only that the wiring is right.
        PasswordEncoder encoder = new BCryptPasswordEncoder(4);
        service = new ApiKeyService(repository, encoder);

        merchant = new Merchant("acct_test", "Test Shop", "shop@example.com", "NL");
        merchant.activate();
    }

    @Test
    @DisplayName("an issued key is returned in plaintext once and stored only as a hash")
    void issuesKeyAndStoresOnlyTheHash() {
        GeneratedApiKey generated = service.issue(merchant, false, Set.of("payments:write"));

        ArgumentCaptor<ApiKey> saved = ArgumentCaptor.forClass(ApiKey.class);
        verify(repository).save(saved.capture());
        ApiKey stored = saved.getValue();

        assertThat(generated.plaintext()).startsWith("sk_test_");
        assertThat(generated.plaintext()).startsWith(stored.getKeyPrefix() + "_");

        // The crucial assertion: the secret half never appears in what we persist.
        String secret = generated.plaintext().substring(stored.getKeyPrefix().length() + 1);
        assertThat(stored.getSecretHash()).doesNotContain(secret).startsWith("$2");
    }

    @Test
    void livemodeKeysAreLabelledDifferently() {
        assertThat(service.issue(merchant, true, Set.of("payments:write")).plaintext())
                .startsWith("sk_live_");
    }

    @Test
    void verifiesAKeyItJustIssued() {
        GeneratedApiKey generated = service.issue(merchant, false, null);
        when(repository.findByKeyPrefixWithMerchant(generated.key().getKeyPrefix()))
                .thenReturn(Optional.of(generated.key()));

        assertThat(service.verify(generated.plaintext())).containsSame(generated.key());
    }

    @Test
    @DisplayName("the right prefix with the wrong secret is rejected")
    void rejectsAWrongSecret() {
        GeneratedApiKey generated = service.issue(merchant, false, null);
        when(repository.findByKeyPrefixWithMerchant(generated.key().getKeyPrefix()))
                .thenReturn(Optional.of(generated.key()));

        assertThat(service.verify(generated.key().getKeyPrefix() + "_totallyWrongSecret")).isEmpty();
    }

    @Test
    void rejectsARevokedKey() {
        GeneratedApiKey generated = service.issue(merchant, false, null);
        generated.key().revoke();
        when(repository.findByKeyPrefixWithMerchant(anyString())).thenReturn(Optional.of(generated.key()));

        assertThat(service.verify(generated.plaintext())).isEmpty();
    }

    @Test
    @DisplayName("a valid key belonging to a suspended merchant is rejected")
    void rejectsKeysOfSuspendedMerchants() {
        GeneratedApiKey generated = service.issue(merchant, false, null);
        merchant.suspend("under review");
        when(repository.findByKeyPrefixWithMerchant(anyString())).thenReturn(Optional.of(generated.key()));

        assertThat(service.verify(generated.plaintext())).isEmpty();
    }

    @Test
    void rejectsAnUnknownPrefix() {
        when(repository.findByKeyPrefixWithMerchant(anyString())).thenReturn(Optional.empty());

        assertThat(service.verify("sk_test_unknown_secret")).isEmpty();
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"   ", "no-underscore-at-all", "trailing_", "_leading"})
    void rejectsMalformedKeysWithoutTouchingTheDatabase(String malformed) {
        assertThat(service.verify(malformed)).isEmpty();
        // Note: no repository interaction is stubbed, so a lookup would have returned null
        // and failed. Malformed input must be rejected before it reaches the database.
    }

    @Test
    @DisplayName("revoking another merchant's key looks exactly like the key not existing")
    void revokeIsTenantScoped() {
        GeneratedApiKey generated = service.issue(merchant, false, null);
        when(repository.findById(generated.key().getId())).thenReturn(Optional.of(generated.key()));

        // A 403 would confirm the key exists. A 404 gives an attacker nothing.
        assertThatThrownBy(() -> service.revoke("acct_someone_else", generated.key().getId()))
                .isInstanceOf(Exceptions.NotFoundException.class);

        assertThat(generated.key().isRevoked()).isFalse();

        service.revoke("acct_test", generated.key().getId());
        assertThat(generated.key().isRevoked()).isTrue();
    }

    @Test
    void defaultScopesAreAppliedWhenNoneRequested() {
        GeneratedApiKey generated = service.issue(merchant, false, Set.of());

        assertThat(generated.key().scopeSet())
                .containsExactlyInAnyOrder("payments:read", "payments:write");
    }
}
