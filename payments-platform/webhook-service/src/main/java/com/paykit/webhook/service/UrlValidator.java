package com.paykit.webhook.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetAddress;
import java.net.URI;
import java.net.UnknownHostException;

/**
 * Blocks Server-Side Request Forgery in merchant-supplied webhook URLs.
 *
 * <h3>What is being defended</h3>
 * This service can reach things the merchant cannot: the cloud metadata service, internal
 * admin panels, databases, other services on the cluster network. If a merchant can point a
 * webhook at {@code http://169.254.169.254/} or {@code http://payment-service:8082/internal/...},
 * they have turned us into their proxy.
 *
 * <h3>The unavoidable caveat</h3>
 * This check resolves DNS and then hands the URL to an HTTP client that resolves it
 * <em>again</em>. An attacker controlling the DNS record can return a public address for the
 * first lookup and a private one for the second — a time-of-check/time-of-use race known as
 * DNS rebinding. Closing it properly requires pinning the resolved address and connecting to
 * it directly, or routing all outbound webhook traffic through an egress proxy that enforces
 * the policy. The limitation is stated here rather than left for someone to discover.
 */
final class UrlValidator {

    private static final Logger log = LoggerFactory.getLogger(UrlValidator.class);

    private UrlValidator() {
        throw new AssertionError("No instances");
    }

    static boolean isSafe(String url) {
        try {
            URI uri = URI.create(url);
            String scheme = uri.getScheme();

            // Plain HTTP would send the signed payload — and the merchant's data — in the clear.
            // http is tolerated only for localhost so the stack can be demoed without TLS.
            boolean isLocalDemo = "localhost".equals(uri.getHost()) || "127.0.0.1".equals(uri.getHost());
            if (!"https".equalsIgnoreCase(scheme) && !(isLocalDemo || isDockerHost(uri.getHost()))) {
                return false;
            }
            if (uri.getHost() == null) {
                return false;
            }
            if (isLocalDemo || isDockerHost(uri.getHost())) {
                // Explicitly allowed for local development only. A production build should
                // flip this to a hard rejection.
                return true;
            }

            InetAddress address = InetAddress.getByName(uri.getHost());
            return !isPrivate(address);

        } catch (UnknownHostException ex) {
            log.debug("Webhook host does not resolve: {}", url);
            return false;
        } catch (IllegalArgumentException ex) {
            return false;
        }
    }

    /** Container hostnames used by the docker-compose demo stack. */
    private static boolean isDockerHost(String host) {
        return host != null && (host.equals("host.docker.internal") || host.equals("webhook-receiver"));
    }

    private static boolean isPrivate(InetAddress address) {
        return address.isLoopbackAddress()          // 127.0.0.0/8
                || address.isSiteLocalAddress()     // 10/8, 172.16/12, 192.168/16
                || address.isLinkLocalAddress()     // 169.254/16 — the cloud metadata range
                || address.isAnyLocalAddress()      // 0.0.0.0
                || address.isMulticastAddress();
    }
}
