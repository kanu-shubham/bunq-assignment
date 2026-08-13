package com.example.prep.partnersend.partner;

import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicLong;

/**
 * A partner bank that behaves like a real one: mostly fine, occasionally slow, sometimes
 * unavailable, and every so often flatly rejecting a payment.
 *
 * <p>Exists so the resilience layer can be watched doing its job when you run the app.
 * Tests use their own purpose-built fakes rather than this — a random failure rate is
 * exactly what you do not want in an assertion.
 */
public class SimulatedPartnerBankClient implements PartnerBankClient {

    private final double unavailableRate;
    private final double slowRate;
    private final double rejectRate;
    private final AtomicLong calls = new AtomicLong();

    public SimulatedPartnerBankClient(double unavailableRate, double slowRate, double rejectRate) {
        this.unavailableRate = unavailableRate;
        this.slowRate = slowRate;
        this.rejectRate = rejectRate;
    }

    @Override
    public PartnerAck submit(PaymentInstruction instruction) {
        calls.incrementAndGet();
        double roll = ThreadLocalRandom.current().nextDouble();

        if (roll < unavailableRate) {
            throw PartnerBankException.unavailable("Partner returned 503");
        }
        if (roll < unavailableRate + rejectRate) {
            throw PartnerBankException.rejected("Beneficiary account closed");
        }
        if (roll < unavailableRate + rejectRate + slowRate) {
            sleep(5_000); // longer than the TimeLimiter's 2s budget — will be cut off
        } else {
            sleep(ThreadLocalRandom.current().nextLong(20, 120));
        }
        return PartnerAck.accepted("SCHEME-" + UUID.randomUUID());
    }

    public long callCount() {
        return calls.get();
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            // Restoring the interrupt flag is not a formality: swallowing it silently
            // breaks every layer above that is trying to cancel this work.
            Thread.currentThread().interrupt();
            throw PartnerBankException.timeout("Interrupted while calling partner");
        }
    }
}
