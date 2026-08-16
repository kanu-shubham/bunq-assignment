package com.paykit.payment.idempotency;

import com.paykit.common.error.Exceptions;
import jakarta.servlet.http.HttpServletRequest;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Pointcut;
import org.aspectj.lang.reflect.MethodSignature;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.lang.annotation.Annotation;
import java.util.Optional;

/**
 * The behaviour behind {@link Idempotent}.
 *
 * <h3>What AOP is actually doing</h3>
 * At startup Spring notices that {@code PaymentIntentController} has a method matching this
 * aspect's pointcut, and replaces the bean with a <b>proxy</b>. Callers hold the proxy; the
 * proxy runs this advice and then delegates to the real object. The controller's own code
 * contains not one line about idempotency, yet every annotated endpoint gets it.
 *
 * <h3>The proxy caveat that catches everyone</h3>
 * Because the interception lives in the proxy, an <em>internal</em> call — one method of a bean
 * calling another method of the same bean via {@code this} — bypasses it entirely. The same
 * trap applies to {@code @Transactional}, {@code @Cacheable} and {@code @Async}. If an
 * annotation "does not work", a self-invocation is the first thing to check.
 *
 * <h3>The flow</h3>
 * <pre>
 *   no Idempotency-Key      → proceed unprotected (or reject, if required = true)
 *   key seen, COMPLETED     → replay the stored response, do NOT execute
 *   key seen, different body→ 409 idempotency_conflict
 *   key seen, IN_PROGRESS   → 409, a duplicate is running right now
 *   key unseen              → reserve, execute, store the response
 * </pre>
 */
@Aspect
@Component
@Order(10)
public class IdempotencyAspect {

    private static final Logger log = LoggerFactory.getLogger(IdempotencyAspect.class);
    private static final String KEY_HEADER = "Idempotency-Key";
    private static final String MERCHANT_HEADER = "X-Merchant-Id";
    private static final int MAX_KEY_LENGTH = 128;

    private final IdempotencyService idempotencyService;

    public IdempotencyAspect(IdempotencyService idempotencyService) {
        this.idempotencyService = idempotencyService;
    }

    /** A pointcut names a set of join points; here, "any method annotated with @Idempotent". */
    @Pointcut("@annotation(com.paykit.payment.idempotency.Idempotent)")
    public void idempotentMethod() {
    }

    @Around("idempotentMethod() && @annotation(idempotent)")
    public Object around(ProceedingJoinPoint joinPoint, Idempotent idempotent) throws Throwable {
        HttpServletRequest request = currentRequest();
        if (request == null) {
            return joinPoint.proceed();
        }

        String key = request.getHeader(KEY_HEADER);
        String merchantId = request.getHeader(MERCHANT_HEADER);

        if (key == null || key.isBlank()) {
            if (idempotent.required()) {
                throw new Exceptions.InvalidRequestException(
                        "This endpoint requires an Idempotency-Key header.", KEY_HEADER);
            }
            // Allowed, but the client is now responsible for the consequences of a retry.
            log.debug("{} called without an idempotency key", idempotent.value());
            return joinPoint.proceed();
        }

        if (key.length() > MAX_KEY_LENGTH) {
            throw new Exceptions.InvalidRequestException(
                    "Idempotency-Key must be at most %d characters.".formatted(MAX_KEY_LENGTH), KEY_HEADER);
        }
        if (merchantId == null || merchantId.isBlank()) {
            // Unscoped keys would let one merchant read another's stored response.
            throw new Exceptions.AuthenticationException("Merchant context is required.");
        }

        MethodSignature signature = (MethodSignature) joinPoint.getSignature();
        Object requestBody = extractRequestBody(joinPoint, signature);
        String requestHash = idempotencyService.hash(requestBody);

        Optional<IdempotencyRecord> existing = idempotencyService.find(merchantId, key);
        if (existing.isPresent()) {
            return handleExisting(existing.get(), key, requestHash, signature);
        }

        // Reserve first, execute second. If this throws, a duplicate is already in flight.
        idempotencyService.reserve(merchantId, key, requestHash, idempotent.value());

        try {
            Object result = joinPoint.proceed();
            idempotencyService.complete(merchantId, key, 200, result, resourceIdOf(result));
            return result;
        } catch (Throwable failure) {
            // A failed operation must not burn the key — the client's retry is the whole point.
            idempotencyService.release(merchantId, key);
            throw failure;
        }
    }

    private Object handleExisting(IdempotencyRecord record, String key, String requestHash,
                                  MethodSignature signature) {
        if (!record.matches(requestHash)) {
            // Same key, different request. Refusing is the only safe answer: replaying the old
            // response would silently ignore what the client actually asked for, and executing
            // the new one would make the key meaningless.
            throw new Exceptions.IdempotencyConflictException(key);
        }
        if (!record.isCompleted()) {
            throw new Exceptions.ConcurrentModificationException("Request with idempotency key", key);
        }
        log.info("Replaying stored response for idempotency key {}", key);
        return idempotencyService.replay(record, signature.getReturnType());
    }

    /** Finds the {@code @RequestBody} parameter — the part of the request that must match on retry. */
    private static Object extractRequestBody(ProceedingJoinPoint joinPoint, MethodSignature signature) {
        Annotation[][] parameterAnnotations = signature.getMethod().getParameterAnnotations();
        Object[] args = joinPoint.getArgs();

        for (int i = 0; i < parameterAnnotations.length; i++) {
            for (Annotation annotation : parameterAnnotations[i]) {
                if (annotation instanceof RequestBody) {
                    return args[i];
                }
            }
        }
        // No body (e.g. POST /{id}/confirm with no payload): the path itself identifies the work.
        return joinPoint.getSignature().toShortString();
    }

    /** Best-effort extraction of the created resource's id, purely for traceability. */
    private static String resourceIdOf(Object result) {
        if (result == null) {
            return null;
        }
        try {
            var accessor = result.getClass().getMethod("id");
            Object value = accessor.invoke(result);
            return value == null ? null : value.toString();
        } catch (ReflectiveOperationException ex) {
            return null;
        }
    }

    private static HttpServletRequest currentRequest() {
        var attributes = RequestContextHolder.getRequestAttributes();
        return attributes instanceof ServletRequestAttributes servletAttributes
                ? servletAttributes.getRequest()
                : null;
    }
}
