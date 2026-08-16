package com.paykit.gateway.config;

import org.springframework.cloud.client.loadbalancer.LoadBalanced;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
public class WebClientConfig {

    /**
     * SPRING CLOUD CONCEPT — {@code @LoadBalanced} swaps in a filter that understands
     * {@code lb://service-id} URIs. The builder asks the discovery client for the live
     * instances of {@code auth-service} and picks one (round-robin by default) on every call.
     *
     * <p>This is client-side load balancing: no proxy in the middle, one less hop, and the
     * caller notices a scaled-up instance as soon as Eureka does.
     */
    @Bean
    @LoadBalanced
    public WebClient.Builder loadBalancedWebClientBuilder() {
        return WebClient.builder();
    }
}
