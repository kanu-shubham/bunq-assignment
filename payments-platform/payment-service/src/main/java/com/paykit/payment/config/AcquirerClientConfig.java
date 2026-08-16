package com.paykit.payment.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.boot.http.client.ClientHttpRequestFactorySettings;
import org.springframework.boot.http.client.ClientHttpRequestFactoryBuilder;
import org.springframework.web.client.RestClient;

import java.time.Duration;

@Configuration
public class AcquirerClientConfig {

    @ConfigurationProperties(prefix = "paykit.acquirer")
    public record AcquirerProperties(String baseUrl, Duration connectTimeout, Duration readTimeout) {

        public AcquirerProperties {
            if (baseUrl == null || baseUrl.isBlank()) {
                baseUrl = "http://localhost:8085";
            }
            if (connectTimeout == null) {
                connectTimeout = Duration.ofSeconds(2);
            }
            if (readTimeout == null) {
                readTimeout = Duration.ofSeconds(5);
            }
        }
    }

    /**
     * A {@link RestClient} with <em>both</em> timeouts set.
     *
     * <p>The default is no read timeout at all: a socket that opens and then goes silent
     * blocks the calling thread indefinitely. Under load, that is how a thread pool fills up
     * and a healthy service stops answering because of an unhealthy dependency. The two are
     * different failures — connect timeout means "cannot reach it", read timeout means
     * "reached it, and it is not answering" — and both need a bound.
     */
    @Bean
    public RestClient acquirerRestClient(AcquirerProperties properties, RestClient.Builder builder) {
        ClientHttpRequestFactory requestFactory = ClientHttpRequestFactoryBuilder.detect()
                .build(ClientHttpRequestFactorySettings.defaults()
                        .withConnectTimeout(properties.connectTimeout())
                        .withReadTimeout(properties.readTimeout()));

        return builder
                .baseUrl(properties.baseUrl())
                .requestFactory(requestFactory)
                .build();
    }
}
