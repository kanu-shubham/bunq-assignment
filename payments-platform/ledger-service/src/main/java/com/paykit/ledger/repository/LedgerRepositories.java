package com.paykit.ledger.repository;

import com.paykit.common.money.Currency;
import com.paykit.ledger.domain.AccountType;
import com.paykit.ledger.domain.LedgerAccount;
import com.paykit.ledger.domain.LedgerEntry;
import com.paykit.ledger.domain.ProcessedEvent;
import jakarta.persistence.LockModeType;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

import java.util.List;
import java.util.Optional;

/** The ledger's repositories, grouped so the data access surface is visible in one file. */
public final class LedgerRepositories {

    private LedgerRepositories() {
        throw new AssertionError("No instances");
    }

    public interface LedgerAccountRepository extends JpaRepository<LedgerAccount, String> {

        /**
         * Locks the account row before its cached balance is updated, so two events for the
         * same merchant arriving on different partitions cannot both read the old balance.
         */
        @Lock(LockModeType.PESSIMISTIC_WRITE)
        @Query("SELECT a FROM LedgerAccount a WHERE a.id = :id")
        Optional<LedgerAccount> findForUpdate(String id);

        List<LedgerAccount> findByMerchantId(String merchantId);

        Optional<LedgerAccount> findByMerchantIdAndAccountTypeAndCurrency(
                String merchantId, AccountType accountType, Currency currency);
    }

    public interface LedgerEntryRepository extends JpaRepository<LedgerEntry, String> {

        List<LedgerEntry> findByTransactionIdOrderByDirection(String transactionId);

        Page<LedgerEntry> findByMerchantIdOrderByCreatedAtDesc(String merchantId, Pageable pageable);

        Page<LedgerEntry> findByAccountIdOrderByCreatedAtDesc(String accountId, Pageable pageable);

        /**
         * Recomputes a balance from the postings themselves.
         *
         * <p>This is the audit query: if it disagrees with {@code LedgerAccount.balanceMinor},
         * something is wrong and the postings are the ones to believe. A ledger you cannot
         * verify independently is just a number in a column.
         */
        @Query("""
                SELECT COALESCE(SUM(CASE WHEN e.direction = com.paykit.ledger.domain.Direction.DEBIT
                                         THEN e.amountMinor ELSE -e.amountMinor END), 0)
                FROM LedgerEntry e
                WHERE e.accountId = :accountId
                """)
        long sumSignedDebits(String accountId);

        long countBySourceEventId(String sourceEventId);
    }

    public interface ProcessedEventRepository extends JpaRepository<ProcessedEvent, String> {
    }
}
