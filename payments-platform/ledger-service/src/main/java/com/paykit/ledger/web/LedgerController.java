package com.paykit.ledger.web;

import com.paykit.common.money.Currency;
import com.paykit.ledger.domain.LedgerAccount;
import com.paykit.ledger.domain.LedgerEntry;
import com.paykit.ledger.service.BalanceService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.data.domain.Page;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.List;

@RestController
@Tag(name = "Balance and Ledger", description = "What the merchant is owed, and why")
public class LedgerController {

    private final BalanceService balanceService;

    public LedgerController(BalanceService balanceService) {
        this.balanceService = balanceService;
    }

    @GetMapping("/v1/balance")
    @Operation(summary = "Current balances per account and currency")
    public BalanceResponse balance(@RequestHeader("X-Merchant-Id") String merchantId) {
        List<AccountBalance> accounts = balanceService.accountsFor(merchantId).stream()
                .map(AccountBalance::from)
                .toList();
        return new BalanceResponse("balance", merchantId, accounts);
    }

    @GetMapping("/v1/ledger/entries")
    @Operation(summary = "The postings behind the balance, newest first")
    public EntriesResponse entries(@RequestHeader("X-Merchant-Id") String merchantId,
                                   @RequestParam(defaultValue = "0") int page,
                                   @RequestParam(defaultValue = "20") int limit) {
        Page<LedgerEntry> entries = balanceService.entriesFor(merchantId, page, limit);
        return new EntriesResponse(
                "list",
                entries.map(EntryResponse::from).getContent(),
                entries.hasNext(),
                entries.getTotalElements());
    }

    /**
     * Exposes the self-audit. Useful in a demo, and a genuinely good operational endpoint —
     * though in production it belongs behind an internal route rather than the merchant API.
     */
    @GetMapping("/v1/ledger/verify")
    @Operation(summary = "Recompute balances from the postings and report any drift")
    public List<BalanceService.BalanceCheck> verify(@RequestHeader("X-Merchant-Id") String merchantId) {
        return balanceService.verify(merchantId);
    }

    public record BalanceResponse(String object, String merchantId, List<AccountBalance> accounts) {
    }

    public record AccountBalance(String accountId, String accountType, long amount, Currency currency) {

        static AccountBalance from(LedgerAccount account) {
            return new AccountBalance(
                    account.getId(),
                    account.getAccountType().name().toLowerCase(),
                    account.getBalance().minorUnits(),
                    account.getCurrency());
        }
    }

    public record EntriesResponse(String object, List<EntryResponse> data, boolean hasMore, long totalCount) {
    }

    public record EntryResponse(String id, String transactionId, String accountId, String direction,
                                long amount, Currency currency, String sourceType,
                                String referenceId, Instant createdAt) {

        static EntryResponse from(LedgerEntry entry) {
            return new EntryResponse(
                    entry.getId(), entry.getTransactionId(), entry.getAccountId(),
                    entry.getDirection().name().toLowerCase(), entry.getAmount().minorUnits(),
                    entry.getCurrency(), entry.getSourceType(), entry.getReferenceId(),
                    entry.getCreatedAt());
        }
    }
}
