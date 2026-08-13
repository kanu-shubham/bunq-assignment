package com.example.prep.partnersend.partner;

import com.example.prep.partnersend.domain.Money;

/**
 * What we send downstream.
 *
 * <p>{@code idempotencyKey} is deliberately derived from our transfer id rather than
 * generated per attempt. It must stay <em>stable across retries</em> — that is the entire
 * point. A key regenerated on each attempt makes every retry look like a brand-new payment
 * to the partner, which converts a transient network blip into a double credit.
 */
public record PaymentInstruction(
        String transferId, String beneficiaryIban, Money amount, String idempotencyKey) {

    public static PaymentInstruction forTransfer(String transferId, String beneficiaryIban, Money amount) {
        return new PaymentInstruction(transferId, beneficiaryIban, amount, "txf-" + transferId);
    }
}
