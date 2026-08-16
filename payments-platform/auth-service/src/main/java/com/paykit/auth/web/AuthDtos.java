package com.paykit.auth.web;

import com.paykit.auth.domain.ApiKey;
import com.paykit.auth.domain.Merchant;
import com.paykit.auth.service.GeneratedApiKey;
import com.paykit.auth.service.MerchantService;
import com.paykit.auth.service.TokenService;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.Set;

/**
 * Request and response shapes for the HTTP layer.
 *
 * <h3>Why not just expose the entities?</h3>
 * Three reasons, all of which bite eventually:
 * <ol>
 *   <li><b>Leakage.</b> Serialising {@link ApiKey} directly would publish {@code secretHash}
 *       to the internet. DTOs make that impossible by construction.</li>
 *   <li><b>Coupling.</b> Renaming a column would silently break every client.</li>
 *   <li><b>Lazy loading.</b> Jackson touching a lazy association outside a transaction throws
 *       LazyInitializationException — a classic 500 that only appears in production.</li>
 * </ol>
 *
 * <p>The {@code from(...)} factories keep the entity-to-DTO mapping in one place instead of
 * scattered across controllers.
 */
public final class AuthDtos {

    private AuthDtos() {
        throw new AssertionError("No instances");
    }

    // ---------- requests ----------

    /**
     * Bean validation annotations are the first line of defence. They run before the
     * controller body does, and {@code GlobalExceptionHandler} turns a failure into a
     * field-by-field 400 — so no business method ever sees a blank email.
     */
    public record RegisterMerchantRequest(
            @NotBlank @Size(max = 200) String name,
            @NotBlank @Email @Size(max = 255) String email,
            @NotBlank @Pattern(regexp = "^[A-Z]{2}$", message = "must be a two-letter ISO country code")
            String countryCode) {
    }

    public record TokenRequest(@NotBlank String apiKey) {
    }

    public record CreateApiKeyRequest(boolean livemode, Set<String> scopes) {
    }

    public record VerifyApiKeyRequest(@NotBlank String apiKey) {
    }

    // ---------- responses ----------

    public record MerchantResponse(
            String id, String object, String name, String email, String countryCode,
            String status, Instant createdAt) {

        public static MerchantResponse from(Merchant merchant) {
            return new MerchantResponse(
                    merchant.getId(), "merchant", merchant.getName(), merchant.getEmail(),
                    merchant.getCountryCode(), merchant.getStatus().name().toLowerCase(),
                    merchant.getCreatedAt());
        }
    }

    /** Note the absence of any secret: this is the safe, listable view of a key. */
    public record ApiKeyResponse(
            String id, String object, String keyPrefix, boolean livemode, boolean revoked,
            List<String> scopes, Instant lastUsedAt, Instant createdAt) {

        public static ApiKeyResponse from(ApiKey key) {
            return new ApiKeyResponse(
                    key.getId(), "api_key", key.getKeyPrefix(), key.isLivemode(), key.isRevoked(),
                    List.copyOf(key.scopeSet()), key.getLastUsedAt(), key.getCreatedAt());
        }
    }

    /** Returned exactly once, at creation. There is no endpoint that can show it again. */
    public record CreatedApiKeyResponse(ApiKeyResponse key, String secret, String warning) {

        public static CreatedApiKeyResponse from(GeneratedApiKey generated) {
            return new CreatedApiKeyResponse(
                    ApiKeyResponse.from(generated.key()),
                    generated.plaintext(),
                    "Store this secret now — it is hashed on our side and cannot be shown again.");
        }
    }

    public record OnboardingResponse(MerchantResponse merchant, CreatedApiKeyResponse apiKey) {

        public static OnboardingResponse from(MerchantService.Onboarding onboarding) {
            return new OnboardingResponse(
                    MerchantResponse.from(onboarding.merchant()),
                    CreatedApiKeyResponse.from(onboarding.apiKey()));
        }
    }

    public record TokenResponse(String accessToken, String tokenType, long expiresIn, Instant expiresAt) {

        public static TokenResponse from(TokenService.IssuedToken token) {
            return new TokenResponse(
                    token.accessToken(), token.tokenType(), token.expiresIn(), token.expiresAt());
        }
    }

    /** The internal contract the gateway depends on. Keep it stable. */
    public record VerifyApiKeyResponse(String merchantId, String keyId, boolean livemode, List<String> scopes) {

        public static VerifyApiKeyResponse from(ApiKey key) {
            return new VerifyApiKeyResponse(
                    key.getMerchant().getId(), key.getId(), key.isLivemode(), List.copyOf(key.scopeSet()));
        }
    }
}
