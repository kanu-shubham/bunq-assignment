package com.example.prep.partnersend;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * A miniature payment-initiation service, built as a rehearsal space for distributed-systems
 * and fault-tolerance interview questions.
 *
 * <p>{@code @SpringBootApplication} bundles three things: {@code @Configuration} (this class
 * can define beans), {@code @EnableAutoConfiguration} (Boot infers beans from what is on the
 * classpath — H2 present, so a DataSource appears) and {@code @ComponentScan} (this package
 * and everything under it is searched for {@code @Component}, {@code @Service},
 * {@code @RestController} and friends).
 */
@SpringBootApplication
@EnableScheduling
public class PartnerSendApplication {

    public static void main(String[] args) {
        SpringApplication.run(PartnerSendApplication.class, args);
    }
}
