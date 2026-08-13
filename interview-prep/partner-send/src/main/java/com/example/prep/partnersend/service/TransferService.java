package com.example.prep.partnersend.service;

import com.example.prep.partnersend.domain.Money;
import com.example.prep.partnersend.domain.Transfer;
import com.example.prep.partnersend.domain.TransferRepository;
import com.example.prep.partnersend.outbox.OutboxEvent;
import com.example.prep.partnersend.outbox.OutboxRepository;
import java.time.Clock;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Accepts a transfer and records the fact that it happened — atomically.
 *
 * <p>Note what this method does <em>not</em> do: it does not call the partner bank. The
 * write transaction ends the moment the local state is durable, and the slow, failure-prone
 * network call happens later, driven by the outbox. That separation is the whole design:
 *
 * <ul>
 *   <li>Holding a database transaction open across a network call is how connection pools
 *       die. A partner taking 30 seconds means 30 seconds of a held connection per request,
 *       and the pool is exhausted long before the partner recovers — taking down every
 *       unrelated endpoint with it.</li>
 *   <li>If we called the partner inside the transaction and the commit then failed, we would
 *       have moved real money with no local record of it. The reverse ordering is not better,
 *       it just fails differently.</li>
 * </ul>
 *
 * <p>So the synchronous path is: validate, persist, emit an event, return {@code 202
 * Accepted}. The client gets a fast, honest answer — "recorded, not yet settled" — which is
 * also the truthful description of a payment that has just been submitted.
 */
@Service
public class TransferService {

    private final TransferRepository transfers;
    private final OutboxRepository outbox;
    private final Clock clock;

    public TransferService(TransferRepository transfers, OutboxRepository outbox, Clock clock) {
        this.transfers = transfers;
        this.outbox = outbox;
        this.clock = clock;
    }

    /**
     * One transaction, two tables. If anything throws, both the transfer and its event
     * disappear together — which is exactly the guarantee the dual-write problem denies you
     * when the second write goes to a message broker instead of the same database.
     */
    @Transactional
    public Transfer acceptTransfer(String partnerId, String partnerReference, Money amount) {
        Transfer transfer = Transfer.receive(partnerId, partnerReference, amount, clock.instant());
        transfers.save(transfer);

        outbox.save(OutboxEvent.of(
                transfer.getId(),
                "transfer.received",
                """
                {"transferId":"%s","partnerId":"%s","amountMinorUnits":%d,"currency":"%s","status":"%s"}"""
                        .formatted(
                                transfer.getId(),
                                partnerId,
                                amount.minorUnits(),
                                amount.currency().getCurrencyCode(),
                                transfer.getStatus()),
                clock.instant()));

        return transfer;
    }

    @Transactional(readOnly = true)
    public Transfer requireTransfer(String id) {
        return transfers.findById(id)
                .orElseThrow(() -> new TransferNotFoundException(id));
    }

    public static class TransferNotFoundException extends RuntimeException {
        public TransferNotFoundException(String id) {
            super("No transfer with id " + id);
        }
    }
}
