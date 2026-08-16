package com.paykit.ledger;

import com.paykit.common.event.PaymentEvent;
import com.paykit.common.event.PaymentEvents;
import com.paykit.common.money.Currency;
import com.paykit.common.money.Money;
import com.paykit.ledger.domain.AccountType;
import com.paykit.ledger.domain.Direction;
import com.paykit.ledger.domain.LedgerAccount;
import com.paykit.ledger.domain.LedgerEntry;
import com.paykit.ledger.repository.LedgerRepositories.LedgerAccountRepository;
import com.paykit.ledger.repository.LedgerRepositories.LedgerEntryRepository;
import com.paykit.ledger.repository.LedgerRepositories.ProcessedEventRepository;
import com.paykit.ledger.service.LedgerPostingService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The accounting rules.
 *
 * <p>Every test here ultimately asserts one thing: debits equal credits. If that ever stops
 * being true, the platform's books do not balance and no amount of downstream reporting
 * can fix it.
 */
class LedgerPostingServiceTest {

    private LedgerAccountRepository accountRepository;
    private LedgerEntryRepository entryRepository;
    private ProcessedEventRepository processedEventRepository;
    private LedgerPostingService service;

    /** Stands in for the accounts table so balances can be asserted after posting. */
    private Map<String, LedgerAccount> accounts;

    @BeforeEach
    void setUp() {
        accountRepository = mock(LedgerAccountRepository.class);
        entryRepository = mock(LedgerEntryRepository.class);
        processedEventRepository = mock(ProcessedEventRepository.class);
        accounts = new HashMap<>();

        when(accountRepository.findForUpdate(anyString()))
                .thenAnswer(inv -> Optional.ofNullable(accounts.get(inv.<String>getArgument(0))));
        when(accountRepository.save(any(LedgerAccount.class))).thenAnswer(inv -> {
            LedgerAccount account = inv.getArgument(0);
            accounts.put(account.getId(), account);
            return account;
        });
        when(processedEventRepository.existsById(anyString())).thenReturn(false);

        service = new LedgerPostingService(accountRepository, entryRepository, processedEventRepository);
    }

    @Test
    @DisplayName("a successful payment: debit clearing, credit merchant payable + fee revenue")
    void successfulPaymentBalances() {
        PaymentEvent event = new PaymentEvents.PaymentSucceeded(
                "evt_1", "acct_1", "pi_1", "ch_1",
                Money.of(10_000L, Currency.EUR), Money.of(320L, Currency.EUR),
                "pm_1", "visa", "4242", "acqref_1", Instant.now());

        assertThat(service.apply(event)).isTrue();

        List<LedgerEntry> entries = capturedEntries();
        assertThat(entries).hasSize(3);
        assertDebitsEqualCredits(entries);

        assertThat(sumFor(entries, Direction.DEBIT)).isEqualTo(10_000L);
        assertThat(sumFor(entries, Direction.CREDIT)).isEqualTo(10_000L);

        // Every entry in one transaction shares a transaction id — that grouping is what makes
        // "show me both sides of this movement" a query rather than a guess.
        assertThat(entries.stream().map(LedgerEntry::getTransactionId).distinct()).hasSize(1);

        assertThat(balanceOf(AccountType.MERCHANT_PAYABLE, "acct_1")).isEqualTo(9_680L);
        assertThat(balanceOf(AccountType.PLATFORM_FEE_REVENUE, "platform")).isEqualTo(320L);
        assertThat(balanceOf(AccountType.CARD_NETWORK_CLEARING, "platform")).isEqualTo(10_000L);
    }

    @Test
    @DisplayName("a refund reverses the payment, leaving every account back at zero")
    void refundReversesThePayment() {
        service.apply(new PaymentEvents.PaymentSucceeded(
                "evt_1", "acct_1", "pi_1", "ch_1",
                Money.of(10_000L, Currency.EUR), Money.of(320L, Currency.EUR),
                "pm_1", "visa", "4242", "acqref_1", Instant.now()));

        service.apply(new PaymentEvents.RefundSucceeded(
                "evt_2", "acct_1", "pi_1", "re_1", "ch_1",
                Money.of(10_000L, Currency.EUR), Money.of(320L, Currency.EUR),
                "REQUESTED_BY_CUSTOMER", Instant.now()));

        assertThat(balanceOf(AccountType.MERCHANT_PAYABLE, "acct_1")).isZero();
        assertThat(balanceOf(AccountType.PLATFORM_FEE_REVENUE, "platform")).isZero();
        assertThat(balanceOf(AccountType.CARD_NETWORK_CLEARING, "platform")).isZero();
    }

    @Test
    @DisplayName("a partial refund leaves the merchant with the untouched remainder")
    void partialRefund() {
        service.apply(new PaymentEvents.PaymentSucceeded(
                "evt_1", "acct_1", "pi_1", "ch_1",
                Money.of(10_000L, Currency.EUR), Money.of(320L, Currency.EUR),
                "pm_1", "visa", "4242", "acqref_1", Instant.now()));

        service.apply(new PaymentEvents.RefundSucceeded(
                "evt_2", "acct_1", "pi_1", "re_1", "ch_1",
                Money.of(4_000L, Currency.EUR), Money.of(128L, Currency.EUR),
                "REQUESTED_BY_CUSTOMER", Instant.now()));

        // 9,680 credited, then 3,872 (4,000 - 128) debited back.
        assertThat(balanceOf(AccountType.MERCHANT_PAYABLE, "acct_1")).isEqualTo(5_808L);
        assertThat(balanceOf(AccountType.PLATFORM_FEE_REVENUE, "platform")).isEqualTo(192L);
        assertThat(balanceOf(AccountType.CARD_NETWORK_CLEARING, "platform")).isEqualTo(6_000L);
    }

    @Test
    @DisplayName("a redelivered event is ignored — Kafka is at-least-once, so this WILL happen")
    void duplicateEventIsSkipped() {
        when(processedEventRepository.existsById("evt_dup")).thenReturn(true);

        boolean posted = service.apply(new PaymentEvents.PaymentSucceeded(
                "evt_dup", "acct_1", "pi_1", "ch_1",
                Money.of(10_000L, Currency.EUR), Money.of(320L, Currency.EUR),
                "pm_1", "visa", "4242", "acqref_1", Instant.now()));

        assertThat(posted).isFalse();
        verify(entryRepository, never()).saveAll(any());
    }

    @Test
    @DisplayName("events that move no money produce no postings, but are still recorded as seen")
    void nonFinancialEventsProduceNoEntries() {
        assertThat(service.apply(new PaymentEvents.PaymentIntentCreated(
                "evt_3", "acct_1", "pi_1", Money.of(5_000L, Currency.EUR),
                "cus_1", "desc", Map.of(), Instant.now()))).isFalse();

        assertThat(service.apply(new PaymentEvents.PaymentFailed(
                "evt_4", "acct_1", "pi_1", Money.of(5_000L, Currency.EUR),
                "card_declined", "declined", 1, Instant.now()))).isFalse();

        assertThat(service.apply(new PaymentEvents.PaymentCanceled(
                "evt_5", "acct_1", "pi_1", Money.of(5_000L, Currency.EUR),
                "abandoned", Instant.now()))).isFalse();

        verify(entryRepository, never()).saveAll(any());
        // Still marked processed, so a replay does not reconsider them.
        verify(processedEventRepository, org.mockito.Mockito.times(3)).save(any());
    }

    @Test
    @DisplayName("a zero-fee payment still balances")
    void zeroFeeStillBalances() {
        service.apply(new PaymentEvents.PaymentSucceeded(
                "evt_6", "acct_1", "pi_1", "ch_1",
                Money.of(2_500L, Currency.USD), Money.zero(Currency.USD),
                "pm_1", "visa", "4242", "acqref_1", Instant.now()));

        assertDebitsEqualCredits(capturedEntries());
        assertThat(balanceOf(AccountType.MERCHANT_PAYABLE, "acct_1")).isEqualTo(2_500L);
    }

    @Test
    @DisplayName("accounts are per merchant and per currency, never merged")
    void accountsAreScopedByMerchantAndCurrency() {
        service.apply(new PaymentEvents.PaymentSucceeded(
                "evt_7", "acct_a", "pi_1", "ch_1",
                Money.of(1_000L, Currency.EUR), Money.of(59L, Currency.EUR),
                "pm_1", "visa", "4242", "acqref_1", Instant.now()));
        service.apply(new PaymentEvents.PaymentSucceeded(
                "evt_8", "acct_b", "pi_2", "ch_2",
                Money.of(2_000L, Currency.USD), Money.of(88L, Currency.USD),
                "pm_2", "visa", "4242", "acqref_2", Instant.now()));

        assertThat(balanceOf(AccountType.MERCHANT_PAYABLE, "acct_a")).isEqualTo(941L);
        assertThat(balanceOf(AccountType.MERCHANT_PAYABLE, "acct_b")).isEqualTo(1_912L);

        // Two currencies means two clearing accounts — never a mixed-currency total.
        assertThat(accounts.keySet())
                .contains("acc_platform_card_network_clearing_EUR",
                          "acc_platform_card_network_clearing_USD");
    }

    // ---- helpers ----

    @SuppressWarnings("unchecked")
    private List<LedgerEntry> capturedEntries() {
        ArgumentCaptor<List<LedgerEntry>> captor = ArgumentCaptor.forClass(List.class);
        verify(entryRepository, org.mockito.Mockito.atLeastOnce()).saveAll(captor.capture());
        return captor.getValue();
    }

    private static void assertDebitsEqualCredits(List<LedgerEntry> entries) {
        assertThat(sumFor(entries, Direction.DEBIT))
                .as("debits must equal credits")
                .isEqualTo(sumFor(entries, Direction.CREDIT));
    }

    private static long sumFor(List<LedgerEntry> entries, Direction direction) {
        return entries.stream()
                .filter(e -> e.getDirection() == direction)
                .mapToLong(e -> e.getAmount().minorUnits())
                .sum();
    }

    private long balanceOf(AccountType type, String scope) {
        return accounts.values().stream()
                .filter(a -> a.getAccountType() == type && a.getMerchantId().equals(scope))
                .mapToLong(a -> a.getBalance().minorUnits())
                .sum();
    }
}
