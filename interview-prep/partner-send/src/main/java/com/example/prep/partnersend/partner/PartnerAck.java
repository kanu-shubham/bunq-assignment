package com.example.prep.partnersend.partner;

/** The partner bank's acknowledgement — their reference is our handle for reconciliation. */
public record PartnerAck(String schemeReference, boolean accepted) {

    public static PartnerAck accepted(String schemeReference) {
        return new PartnerAck(schemeReference, true);
    }
}
