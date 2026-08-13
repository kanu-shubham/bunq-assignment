package com.example.prep.partnersend.partner;

/** The outbound edge of the system: handing a payment instruction to a partner bank. */
@FunctionalInterface
public interface PartnerBankClient {

    /**
     * @param instruction what to pay, and the idempotency key the partner de-duplicates on
     * @return the partner's acknowledgement, including their reference for the payment
     * @throws PartnerBankException classified retryable or not
     */
    PartnerAck submit(PaymentInstruction instruction);
}
