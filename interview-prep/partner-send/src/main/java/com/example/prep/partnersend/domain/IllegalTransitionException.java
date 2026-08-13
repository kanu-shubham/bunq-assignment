package com.example.prep.partnersend.domain;

/** Thrown when something tries to move a transfer along an edge the state machine does not have. */
public class IllegalTransitionException extends RuntimeException {

    private final transient TransferStatus from;
    private final transient TransferStatus to;

    public IllegalTransitionException(String transferId, TransferStatus from, TransferStatus to) {
        super("Transfer %s cannot move %s -> %s (allowed: %s)".formatted(transferId, from, to, from.allowedNext()));
        this.from = from;
        this.to = to;
    }

    public TransferStatus getFrom() {
        return from;
    }

    public TransferStatus getTo() {
        return to;
    }
}
