package com.paykit.common.config;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.paykit.common.web.CorrelationIdFilter;
import com.paykit.common.web.GlobalExceptionHandler;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnClass;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.boot.autoconfigure.jackson.Jackson2ObjectMapperBuilderCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.web.servlet.DispatcherServlet;

/**
 * Wires the shared web concerns into any service that has the servlet stack on its classpath.
 *
 * <p>SPRING CONCEPT — <b>auto-configuration</b>. This class is listed in
 * {@code META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports},
 * so Boot evaluates it at startup <em>without</em> any service having to import it. This is
 * exactly the mechanism behind every {@code spring-boot-starter-*}: conditional beans that
 * appear only when they make sense.
 *
 * <p>The {@code @ConditionalOn*} guards are what make it safe to share this jar with the
 * reactive api-gateway: no {@link DispatcherServlet} on the classpath, no beans contributed.
 *
 * <p>{@code @ConditionalOnMissingBean} means a service can always define its own
 * {@link GlobalExceptionHandler} and win — convention with an escape hatch.
 */
@AutoConfiguration
@ConditionalOnWebApplication(type = ConditionalOnWebApplication.Type.SERVLET)
@ConditionalOnClass(DispatcherServlet.class)
public class CommonWebAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    public CorrelationIdFilter correlationIdFilter() {
        return new CorrelationIdFilter();
    }

    @Bean
    @ConditionalOnMissingBean
    public GlobalExceptionHandler globalExceptionHandler() {
        return new GlobalExceptionHandler();
    }

    /**
     * Public JSON uses {@code snake_case} ({@code payment_intent_id}, {@code has_more}) to match
     * the conventions of the payments APIs developers already know, while the Java code keeps
     * idiomatic {@code camelCase}. Jackson bridges the two so neither side compromises.
     */
    @Bean
    public Jackson2ObjectMapperBuilderCustomizer paykitJacksonCustomizer() {
        return builder -> builder
                .propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
                .failOnUnknownProperties(false)
                .featuresToDisable(
                        com.fasterxml.jackson.databind.SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }
}
