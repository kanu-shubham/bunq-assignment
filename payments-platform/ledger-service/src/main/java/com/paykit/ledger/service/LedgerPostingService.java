package com.paykit.ledger.service;

import com.paykit.common.event.PaymentEvent;
import com.paykit.common.event.PaymentEvents;
import com.paykit.common.money.Money;
import com.paykit.common.util.Ids;
import com.paykit.ledger.domain.AccountType;
import com.paykit.ledger.domain.Direction;
import com.paykit.ledger.domain.LedgerAccount;
import com.paykit.ledger.domain.LedgerEntry;
import com.paykit.ledger.domain.ProcessedEvent;
import com.paykit.ledger.repository.LedgerRepositories.LedgerAccountRepository;
import com.paykit.ledger.repository.LedgerRepositories.LedgerEntryRepository;
import com.paykit.ledger.repository.LedgerRepositories.ProcessedEventRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

/**
 * Turns payment events into balanced double-entry transactions.
 *
 * <h3>The exhaustive switch</h3>
 * {@link PaymentEvent} is a {@code sealed} interface, so the {@code switch} below needs no
 * {@code default} branch — the compiler knows every case is covered. Add a sixth event type
 * and this file <b>stops compiling</b> until someone decides what it means for the books.
 * A {@code default -> ignore} branch would have silently dropped it, and the ledger would be
 * quietly wrong in a way nobody notices until a reconciliation months later.
 *
 * <h3>The transaction boundary</h3>
 * The dedup row, the ledger entries and the account balance updates all commit together.
 * Any partial application would leave the books unbalanced, which is the one thing a ledger
 * must never do.
 */
@Service
public class LedgerPostingService {

    private static final Logger log = LoggerFactory.getLogger(LedgerPostingService.class);

    private final LedgerAccountRepository accountRepository;
    private final LedgerEntryRepository entryRepository;
    private final ProcessedEventRepository processedEventRepository;

    public LedgerPostingService(LedgerAccountRepository accountRepository,
                                LedgerEntryRepository entryRepository,
                                ProcessedEventRepository processedEventRepository) {
        this.accountRepository = accountRepository;
        this.entryRepository = entryRepository;
        this.processedEventRepository = processedEventRepository;
    }

    /**
     * @return true if the event produced postings, false if it was a duplicate or carries
     *         no accounting meaning. Either way the consumer should acknowledge it.
     */
    @Transactional
    public boolean apply(PaymentEvent event) {
        if (processedEventRepository.existsById(event.eventId())) {
            log.debug("Skipping already-processed event {}", event.eventId());
            return false;
        }

        List<LedgerPosting> postings = postingsFor(event);
        if (postings.isEmpty()) {
            // Created and failed payments move no money. Recording that we saw them still
            // matters, so a replay does not reconsider them.
            processedEventRepository.save(new ProcessedEvent(event.eventId(), event.type()));
            return false;
        }

        requireBalanced(postings, event);
        post(event, postings);

        processedEventRepository.save(new ProcessedEvent(event.eventId(), event.type()));
        return true;
    }

    /**
     * The accounting rules, one case per event.
     *
     * <p>Read the postings as sentences. A successful payment: money arrives from the card
     * network (debit an asset), we now owe the merchant their share (credit a liability), and
     * we have earned our fee (credit revenue).
     */
    private List<LedgerPosting> postingsFor(PaymentEvent event) {
        return switch (event) {

            case PaymentEvents.PaymentSucceeded succeeded -> {
                Money gross = succeeded.amount();
                Money fee = succeeded.processingFee();
                Money net = gross.minus(fee);
                String merchantId = succeeded.merchantId();

                yield List.of(
                        // Asset up: the network owes us the full amount.
                        LedgerPosting.debit(AccountType.CARD_NETWORK_CLEARING, gross, merchantId),
                        // Liability up: we owe the merchant their net.
                        LedgerPosting.credit(AccountType.MERCHANT_PAYABLE, net, merchantId),
                        // Revenue up: the fee is ours.
                        LedgerPosting.credit(AccountType.PLATFORM_FEE_REVENUE, fee, merchantId));
            }

            case PaymentEvents.RefundSucceeded refunded -> {
                Money gross = refunded.amount();
                Money feeBack = refunded.feeRefunded();
                Money net = gross.minus(feeBack);
                String merchantId = refunded.merchantId();

                // Exactly the reverse of a payment. Note that we do not delete the original
                // postings — history is immutable; a refund is new history.
                yield List.of(
                        LedgerPosting.debit(AccountType.MERCHANT_PAYABLE, net, merchantId),
                        LedgerPosting.debit(AccountType.PLATFORM_FEE_REVENUE, feeBack, merchantId),
                        LedgerPosting.credit(AccountType.CARD_NETWORK_CLEARING, gross, merchantId));
            }

            // No money has moved yet, so there is nothing to post. Stated explicitly rather
            // than swept into a default branch, so the intent is on the record.
            case PaymentEvents.PaymentIntentCreated ignored -> List.of();
            case PaymentEvents.PaymentFailed ignored -> List.of();
            case PaymentEvents.PaymentCanceled ignored -> List.of();
        };
    }

    /**
     * The fundamental check: debits must equal credits. If this ever throws, the transaction
     * rolls back and the message goes to the DLQ — an unbalanced ledger is not something to
     * "handle gracefully" and carry on from.
     */
    private void requireBalanced(List<LedgerPosting> postings, PaymentEvent event) {
        long debits = postings.stream()
                .filter(p -> p.direction() == Direction.DEBIT)
                .mapToLong(p -> p.amount().minorUnits())
                .sum();
        long credits = postings.stream()
                .filter(p -> p.direction() == Direction.CREDIT)
                .mapToLong(p -> p.amount().minorUnits())
                .sum();

        if (debits != credits) {
            throw new IllegalStateException(
                    "Unbalanced ledger transaction for event %s: debits=%d credits=%d"
                            .formatted(event.eventId(), debits, credits));
        }
    }

    private void post(PaymentEvent event, List<LedgerPosting> postings) {
        String transactionId = Ids.generate("txn");
        List<LedgerEntry> entries = new ArrayList<>(postings.size());

        for (LedgerPosting posting : postings) {
            LedgerAccount account = accountFor(posting);
            account.apply(posting.direction(), posting.amount());

            entries.add(new LedgerEntry(
                    Ids.generate("le"), transactionId, account.getId(), event.merchantId(),
                    posting.direction(), posting.amount(), event.eventId(), event.type(),
                    event.aggregateId()));
        }

        entryRepository.saveAll(entries);
        log.info("Posted transaction {} ({} entries) from {}",
                transactionId, entries.size(), event.type());
    }

    /**
     * Get-or-create, with the row locked for update when it already exists.
     *
     * <p>Two events for the same merchant can be processed concurrently by two consumer
     * threads; without the lock, both read the same balance and one update is lost. The
     * deterministic account id means the create path collides on the primary key rather than
     * producing duplicate accounts.
     */
    private LedgerAccount accountFor(LedgerPosting posting) {
        String accountId = LedgerAccount.idFor(
                posting.merchantId(), posting.accountType(), posting.amount().currency());

        return accountRepository.findForUpdate(accountId)
                .orElseGet(() -> accountRepository.save(new LedgerAccount(
                        posting.accountType().isPerMerchant() ? posting.merchantId() : "platform",
                        posting.accountType(),
                        posting.amount().currency())));
    }
}
