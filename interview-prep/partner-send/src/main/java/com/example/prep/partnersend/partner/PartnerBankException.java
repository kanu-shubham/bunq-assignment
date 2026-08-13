package com.example.prep.partnersend.partner;

/**
 * Failure talking to a partner bank, carrying the one bit that decides everything
 * downstream: <b>may this be retried?</b>
 *
 * <p>Getting this classification wrong is the expensive mistake in payments.
 *
 * <ul>
 *   <li><b>Retryable</b> — 503, connection reset, read timeout, 429. The instruction may or
 *       may not have been acted on, but trying again is legitimate.</li>
 *   <li><b>Not retryable</b> — 400 malformed IBAN, 403 unauthorised, "beneficiary account
 *       closed". The answer will be identical every time, so a retry only burns budget and
 *       adds load to a system that is already telling you no.</li>
 * </ul>
 *
 * <p>The uncomfortable case is {@link #timeout}: it is marked retryable, but a timeout is
 * <em>ambiguous</em>, not a failure. The request may have been fully processed and only the
 * response lost. Retrying is safe here only because every instruction we send carries a
 * client-side idempotency key that the partner honours — the retry either lands as a fresh
 * instruction or is de-duplicated at their end. Take that key away and this retry becomes a
 * duplicate payment. That dependency is worth saying out loud in an interview: <em>retries
 * are a feature of the idempotency design, not of the HTTP client.</em>
 */
public class PartnerBankException extends RuntimeException {

    private final boolean retryable;

    public PartnerBankException(String message, boolean retryable) {
        super(message);
        this.retryable = retryable;
    }

    public PartnerBankException(String message, boolean retryable, Throwable cause) {
        super(message, cause);
        this.retryable = retryable;
    }

    public boolean isRetryable() {
        return retryable;
    }

    public static PartnerBankException unavailable(String message) {
        return new PartnerBankException(message, true);
    }

    public static PartnerBankException timeout(String message) {
        return new PartnerBankException(message, true);
    }

    public static PartnerBankException rejected(String message) {
        return new PartnerBankException(message, false);
    }
}
