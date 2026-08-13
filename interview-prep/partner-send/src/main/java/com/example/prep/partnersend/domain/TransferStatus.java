package com.example.prep.partnersend.domain;

import java.util.Collections;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.Map;
import java.util.Set;

/**
 * The lifecycle of a partner-initiated transfer, as an explicit state machine.
 *
 * <p>This is the same idea as the reducer in the bunq feedback widget — a closed
 * set of states and a closed set of legal transitions — moved server-side and
 * given money-sized consequences. The value of writing it down is that "can this
 * happen?" becomes a lookup instead of an argument.
 *
 * <pre>
 *   RECEIVED ──▶ VALIDATED ──▶ FUNDED ──▶ SUBMITTED ──▶ SETTLED
 *      │             │            │           │            │
 *      └─────────────┴────────────┴───────────┴──▶ FAILED  │
 *                                             └──▶ RETURNED ◀┘
 * </pre>
 *
 * <p>Two details worth defending out loud in an interview:
 *
 * <ul>
 *   <li><b>SETTLED is not terminal.</b> A beneficiary bank can return funds days
 *       later — wrong account, closed account, sanctions hit at their end. A model
 *       where "settled" is the end of the story cannot represent that, and you
 *       find out in production.</li>
 *   <li><b>SUBMITTED is the dangerous state.</b> Once an instruction is on the wire
 *       to a partner bank, a timeout tells you nothing about whether it landed.
 *       Everything downstream of SUBMITTED must be reconciled against the partner's
 *       own record, never inferred from our request's outcome.</li>
 * </ul>
 */
public enum TransferStatus {

    /** Accepted from the partner, persisted, not yet checked. */
    RECEIVED,
    /** Payload, limits, sanctions and beneficiary checks passed. */
    VALIDATED,
    /** Source funds secured — we are now good for the money. */
    FUNDED,
    /** Instruction handed to the partner bank / scheme. Outcome unknown. */
    SUBMITTED,
    /** Confirmed credited by the beneficiary side. */
    SETTLED,
    /** Never left our system. Funds released back to the payer. */
    FAILED,
    /** Left our system and came back. Funds returned to the payer. */
    RETURNED;

    private static final Map<TransferStatus, Set<TransferStatus>> ALLOWED;

    static {
        Map<TransferStatus, Set<TransferStatus>> m = new EnumMap<>(TransferStatus.class);
        m.put(RECEIVED, EnumSet.of(VALIDATED, FAILED));
        m.put(VALIDATED, EnumSet.of(FUNDED, FAILED));
        m.put(FUNDED, EnumSet.of(SUBMITTED, FAILED));
        m.put(SUBMITTED, EnumSet.of(SETTLED, RETURNED, FAILED));
        m.put(SETTLED, EnumSet.of(RETURNED));
        m.put(FAILED, EnumSet.noneOf(TransferStatus.class));
        m.put(RETURNED, EnumSet.noneOf(TransferStatus.class));
        ALLOWED = Collections.unmodifiableMap(m);
    }

    public Set<TransferStatus> allowedNext() {
        return ALLOWED.get(this);
    }

    public boolean canTransitionTo(TransferStatus next) {
        return ALLOWED.get(this).contains(next);
    }

    /** No transition out of here — the row will never change status again. */
    public boolean isTerminal() {
        return ALLOWED.get(this).isEmpty();
    }
}
