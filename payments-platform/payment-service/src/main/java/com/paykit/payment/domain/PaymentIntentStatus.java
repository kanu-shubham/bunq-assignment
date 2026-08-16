package com.paykit.payment.domain;

import java.util.EnumSet;
import java.util.Set;

/**
 * The lifecycle of a payment, as an explicit state machine.
 *
 * <pre>
 *   REQUIRES_CONFIRMATION ──confirm──▶ PROCESSING ──approved──▶ SUCCEEDED   (terminal)
 *            │                              │
 *            │                              └──declined──▶ FAILED ──confirm──▶ PROCESSING
 *            │
 *            └──cancel──▶ CANCELED (terminal)
 * </pre>
 *
 * <h3>Why encode transitions in the enum</h3>
 * The alternative is an {@code if} in whichever service happens to be doing the update — and
 * those {@code if}s drift. Here the rule lives with the thing it constrains, so
 * "can a succeeded payment be cancelled?" has exactly one answer, in one place, and every
 * caller gets it. In a payments system the cost of getting this wrong is double-charging a
 * customer or refunding a payment that never happened.
 *
 * <h3>Why the transition map is built lazily</h3>
 * An enum constant cannot reference another constant in its own constructor — they are not
 * initialised yet. Resolving the successors on first use sidesteps that, which is a genuine
 * Java gotcha worth knowing.
 */
public enum PaymentIntentStatus {

    /** Created, waiting for the client to confirm with a payment method. */
    REQUIRES_CONFIRMATION,

    /** Handed to the acquirer. No further writes until it answers. */
    PROCESSING,

    /** Money captured. Terminal — only a refund changes anything now. */
    SUCCEEDED,

    /** The issuer declined. Not terminal: the customer may retry with another card. */
    FAILED,

    /** Abandoned before capture. Terminal. */
    CANCELED;

    private Set<PaymentIntentStatus> successors;

    private Set<PaymentIntentStatus> successors() {
        if (successors == null) {
            successors = switch (this) {
                case REQUIRES_CONFIRMATION -> EnumSet.of(PROCESSING, CANCELED);
                case PROCESSING -> EnumSet.of(SUCCEEDED, FAILED);
                case FAILED -> EnumSet.of(PROCESSING, CANCELED);
                case SUCCEEDED, CANCELED -> EnumSet.noneOf(PaymentIntentStatus.class);
            };
        }
        return successors;
    }

    public boolean canTransitionTo(PaymentIntentStatus target) {
        return successors().contains(target);
    }

    public boolean isTerminal() {
        return successors().isEmpty();
    }

    /** Only a succeeded payment has money that can be sent back. */
    public boolean isRefundable() {
        return this == SUCCEEDED;
    }

    /** Lower-case wire form, e.g. {@code requires_confirmation}. */
    public String wireValue() {
        return name().toLowerCase();
    }
}
