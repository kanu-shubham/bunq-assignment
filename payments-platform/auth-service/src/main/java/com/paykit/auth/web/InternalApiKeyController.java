package com.paykit.auth.web;

import com.paykit.auth.service.ApiKeyService;
import com.paykit.common.error.Exceptions;
import io.swagger.v3.oas.annotations.Hidden;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * The endpoint the gateway calls to resolve an API key into a merchant.
 *
 * <p>It lives under {@code /internal/**}, which the gateway's routing table does not expose —
 * there is no public path that reaches it. That is defence in depth, not the only defence:
 * in a real cluster this would also be enforced by a NetworkPolicy and mTLS, because
 * "nobody knows the URL" has never been a security control.
 *
 * <p>{@code @Hidden} keeps it out of the published OpenAPI document.
 */
@RestController
@RequestMapping("/internal/api_keys")
@Hidden
public class InternalApiKeyController {

    private final ApiKeyService apiKeyService;

    public InternalApiKeyController(ApiKeyService apiKeyService) {
        this.apiKeyService = apiKeyService;
    }

    @PostMapping("/verify")
    public AuthDtos.VerifyApiKeyResponse verify(@Valid @RequestBody AuthDtos.VerifyApiKeyRequest request) {
        return apiKeyService.verify(request.apiKey())
                .map(AuthDtos.VerifyApiKeyResponse::from)
                .orElseThrow(() -> new Exceptions.AuthenticationException("Invalid API key"));
    }
}
