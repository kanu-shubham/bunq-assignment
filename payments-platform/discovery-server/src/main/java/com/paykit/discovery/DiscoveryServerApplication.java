package com.paykit.discovery;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.netflix.eureka.server.EnableEurekaServer;

/**
 * The service registry — the phone book of the platform.
 *
 * <p>THE PROBLEM IT SOLVES — in a container world, {@code payment-service} might be running on
 * 2 instances today and 9 tomorrow, on IPs nobody chose in advance. Hard-coding
 * {@code http://10.0.3.14:8082} into the gateway breaks the moment that container is replaced.
 *
 * <p>HOW IT WORKS
 * <ol>
 *   <li>Each service <b>registers</b> itself at startup: "I am PAYMENT-SERVICE at 10.0.3.14:8082".</li>
 *   <li>Each service <b>heartbeats</b> every 30s. Miss three and it is evicted from the registry.</li>
 *   <li>Clients <b>fetch</b> the registry and cache it, so a lookup costs nothing at request time.</li>
 * </ol>
 *
 * <p>THE PAYOFF — the gateway routes to {@code lb://payment-service} and Spring Cloud LoadBalancer
 * turns that logical name into a real instance, round-robin across whatever is currently healthy.
 * That is <b>client-side load balancing</b>: no extra network hop, and instant awareness of scaling.
 *
 * <p>CONTRAST — nginx in front of the gateway is <b>server-side load balancing</b>: a dedicated
 * box that clients talk to instead of the servers. This platform uses both, at different layers.
 * See {@code docs/ARCHITECTURE.md}.
 */
@SpringBootApplication
@EnableEurekaServer
public class DiscoveryServerApplication {

    public static void main(String[] args) {
        SpringApplication.run(DiscoveryServerApplication.class, args);
    }
}
