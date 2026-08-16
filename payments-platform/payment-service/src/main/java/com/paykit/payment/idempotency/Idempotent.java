package com.paykit.payment.idempotency;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Marks a controller method as safely retryable via the {@code Idempotency-Key} header.
 *
 * <p>JAVA CONCEPT — a custom annotation is just metadata; on its own it does nothing.
 * {@code @Retention(RUNTIME)} is what makes it visible to reflection, and therefore to
 * {@link IdempotencyAspect}, which supplies the actual behaviour. This separation — declare
 * intent here, implement it once over there — is the entire idea behind AOP.
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface Idempotent {

    /** Logical operation name, stored with the record so keys cannot be reused across endpoints. */
    String value();

    /** When false, a request without an Idempotency-Key is rejected instead of merely unprotected. */
    boolean required() default false;
}
