package com.paykit.auth.service;

import com.paykit.auth.domain.ApiKey;

/**
 * The one and only time the plaintext secret exists outside the caller's hands.
 *
 * <p>Returning it in a dedicated type, rather than on the entity, makes it structurally
 * impossible to leak: {@link ApiKey} has no field that could accidentally be serialised
 * into a JSON response.
 */
public record GeneratedApiKey(ApiKey key, String plaintext) {
}
