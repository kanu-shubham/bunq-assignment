package com.paykit.auth.web;

import com.paykit.auth.domain.ApiKey;
import com.paykit.auth.service.ApiKeyService;
import com.paykit.auth.service.MerchantService;
import com.paykit.auth.service.TokenService;
import com.paykit.common.error.Exceptions;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * The public authentication API, reachable through the gateway at {@code /v1/auth/**}.
 *
 * <p>SPRING MVC — {@code @RestController} is {@code @Controller} plus {@code @ResponseBody}:
 * every return value is serialised to JSON rather than resolved as a view name. Methods stay
 * thin on purpose. A controller's job is HTTP (status codes, headers, binding); business rules
 * belong in the services, where they can be tested without a web layer at all.
 */
@RestController
@RequestMapping("/v1/auth")
@Tag(name = "Authentication", description = "Merchant onboarding, API keys and access tokens")
public class AuthController {

    private final MerchantService merchantService;
    private final ApiKeyService apiKeyService;
    private final TokenService tokenService;

    public AuthController(MerchantService merchantService,
                          ApiKeyService apiKeyService,
                          TokenService tokenService) {
        this.merchantService = merchantService;
        this.apiKeyService = apiKeyService;
        this.tokenService = tokenService;
    }

    /**
     * DEMO CAVEAT — open registration exists so the platform can be tried out in one curl.
     * A real deployment puts this behind an admin credential and a KYC flow.
     */
    @PostMapping("/merchants")
    @Operation(summary = "Register a merchant and issue its first test API key")
    public ResponseEntity<AuthDtos.OnboardingResponse> register(
            @Valid @RequestBody AuthDtos.RegisterMerchantRequest request) {

        MerchantService.Onboarding onboarding =
                merchantService.register(request.name(), request.email(), request.countryCode());

        // 201 Created with a Location header — the HTTP spec's answer to "you made a thing".
        return ResponseEntity
                .created(java.net.URI.create("/v1/auth/merchants/" + onboarding.merchant().getId()))
                .body(AuthDtos.OnboardingResponse.from(onboarding));
    }

    @GetMapping("/merchants/{merchantId}")
    public AuthDtos.MerchantResponse get(@PathVariable String merchantId,
                                         @RequestHeader("X-Merchant-Id") String callerId) {
        // The gateway proved who the caller is; this service enforces what they may see.
        if (!merchantId.equals(callerId)) {
            throw new Exceptions.NotFoundException("merchant", merchantId);
        }
        return AuthDtos.MerchantResponse.from(merchantService.require(merchantId));
    }

    /**
     * Exchanges an API key for a short-lived JWT. Deliberately a POST, not a GET: a secret in
     * a query string ends up in access logs, browser history and Referer headers.
     */
    @PostMapping("/token")
    @Operation(summary = "Exchange an API key for a short-lived bearer token")
    public AuthDtos.TokenResponse token(@Valid @RequestBody AuthDtos.TokenRequest request) {
        ApiKey key = apiKeyService.verify(request.apiKey())
                .orElseThrow(() -> new Exceptions.AuthenticationException(
                        "The provided API key is invalid, revoked, or belongs to an inactive merchant."));

        return AuthDtos.TokenResponse.from(tokenService.issue(key));
    }

    @PostMapping("/api_keys")
    @Operation(summary = "Issue an additional API key for the calling merchant")
    @org.springframework.web.bind.annotation.ResponseStatus(HttpStatus.CREATED)
    public AuthDtos.CreatedApiKeyResponse createKey(
            @RequestHeader("X-Merchant-Id") String merchantId,
            @Valid @RequestBody AuthDtos.CreateApiKeyRequest request) {

        return AuthDtos.CreatedApiKeyResponse.from(apiKeyService.issue(
                merchantService.require(merchantId), request.livemode(), request.scopes()));
    }

    @GetMapping("/api_keys")
    public List<AuthDtos.ApiKeyResponse> listKeys(@RequestHeader("X-Merchant-Id") String merchantId) {
        return apiKeyService.listForMerchant(merchantId).stream()
                .map(AuthDtos.ApiKeyResponse::from)
                .toList();
    }

    @DeleteMapping("/api_keys/{keyId}")
    @org.springframework.web.bind.annotation.ResponseStatus(HttpStatus.NO_CONTENT)
    public void revokeKey(@RequestHeader("X-Merchant-Id") String merchantId, @PathVariable String keyId) {
        apiKeyService.revoke(merchantId, keyId);
    }
}
