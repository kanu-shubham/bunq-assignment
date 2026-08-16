package com.paykit.ledger.service;

import com.paykit.common.money.Money;
import com.paykit.ledger.domain.LedgerAccount;
import com.paykit.ledger.domain.LedgerEntry;
import com.paykit.ledger.repository.LedgerRepositories.LedgerAccountRepository;
import com.paykit.ledger.repository.LedgerRepositories.LedgerEntryRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
public class BalanceService {

    private static final Logger log = LoggerFactory.getLogger(BalanceService.class);

    private final LedgerAccountRepository accountRepository;
    private final LedgerEntryRepository entryRepository;

    public BalanceService(LedgerAccountRepository accountRepository,
                          LedgerEntryRepository entryRepository) {
        this.accountRepository = accountRepository;
        this.entryRepository = entryRepository;
    }

    @Transactional(readOnly = true)
    public List<LedgerAccount> accountsFor(String merchantId) {
        return accountRepository.findByMerchantId(merchantId);
    }

    @Transactional(readOnly = true)
    public Page<LedgerEntry> entriesFor(String merchantId, int page, int limit) {
        return entryRepository.findByMerchantIdOrderByCreatedAtDesc(
                merchantId, PageRequest.of(Math.max(page, 0), Math.clamp(limit, 1, 100)));
    }

    /**
     * Recomputes each account's balance from its postings and compares it with the cached
     * value.
     *
     * <p>This is the audit that makes the cached balance trustworthy. Any real payments system
     * runs something like it on a schedule, because the interesting failures are the ones that
     * do not throw: a lost update, a partially applied transaction, a bug in a new event type.
     * "The numbers looked fine" is not a control.
     */
    @Transactional(readOnly = true)
    public List<BalanceCheck> verify(String merchantId) {
        return accountRepository.findByMerchantId(merchantId).stream()
                .map(account -> {
                    long computed = entryRepository.sumSignedDebits(account.getId());
                    // sumSignedDebits is debit-positive; a credit-increasing account's balance
                    // is the negative of that sum.
                    long expected = account.getAccountType().signFor(
                            com.paykit.ledger.domain.Direction.DEBIT) * computed;
                    boolean consistent = expected == account.getBalance().minorUnits();

                    if (!consistent) {
                        log.error("Ledger drift on account {}: cached={} computed={}",
                                account.getId(), account.getBalance().minorUnits(), expected);
                    }
                    return new BalanceCheck(
                            account.getId(),
                            account.getAccountType().name(),
                            account.getBalance(),
                            Money.of(expected, account.getCurrency()),
                            consistent);
                })
                .toList();
    }

    public record BalanceCheck(String accountId, String accountType, Money cachedBalance,
                               Money computedBalance, boolean consistent) {
    }
}
