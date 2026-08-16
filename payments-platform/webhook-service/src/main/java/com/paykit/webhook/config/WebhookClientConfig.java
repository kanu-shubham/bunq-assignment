package com.paykit.webhook.config;

import io.netty.channel.ChannelOption;
import io.netty.handler.timeout.ReadTimeoutHandler;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.client.HttpClient;

import java.util.concurrent.TimeUnit;

@Configuration
public class WebhookClientConfig {

    /**
     * The HTTP client used to call merchant servers, configured defensively.
     *
     * <ul>
     *   <li><b>Timeouts</b> — connect and read. Merchant endpoints are not ours to trust.</li>
     *   <li><b>No redirect following</b> — a redirect is an SSRF bypass: the URL passes
     *       validation, then 302s to {@code http://169.254.169.254/}. Never follow redirects
     *       to a destination the caller supplied.</li>
     *   <li><b>Bounded response buffer</b> — we do not read the body at all, but a client that
     *       would happily buffer a 2GB response is an out-of-memory kill waiting to happen.</li>
     * </ul>
     */
    @Bean
    public WebClient webhookWebClient(WebhookProperties properties) {
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 5_000)
                .followRedirect(false)
                .responseTimeout(properties.requestTimeout())
                .doOnConnected(connection -> connection.addHandlerLast(
                        new ReadTimeoutHandler(properties.requestTimeout().toSeconds(), TimeUnit.SECONDS)));

        return WebClient.builder()
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .codecs(codecs -> codecs.defaultCodecs().maxInMemorySize(256 * 1024))
                .build();
    }
}
