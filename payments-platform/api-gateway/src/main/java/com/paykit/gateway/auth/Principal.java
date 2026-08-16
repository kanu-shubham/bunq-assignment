package com.paykit.gateway.auth;

import java.util.Set;

/**
 * Who is calling, once the credential has been verified.
 *
 * <p>This is the only thing the gateway forwards downstream: services never see the raw
 * API key or JWT, only the merchant it resolved to. That way a compromised internal service
 * cannot replay a customer credential.
 */
public record Principal(String merchantId, String keyId, boolean livemode, Set<String> scopes) {

    public boolean hasScope(String scope) {
        return scopes.contains(scope) || scopes.contains("*");
    }
}
