package com.paykit.ledger;

import com.paykit.ledger.domain.AccountType;
import com.paykit.ledger.domain.Direction;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The bit of accounting that everyone gets backwards at least once.
 */
class AccountTypeTest {

    @Test
    @DisplayName("assets increase on the debit side")
    void assetsIncreaseOnDebit() {
        assertThat(AccountType.CARD_NETWORK_CLEARING.increasesOn()).isEqualTo(Direction.DEBIT);
        assertThat(AccountType.CARD_NETWORK_CLEARING.signFor(Direction.DEBIT)).isEqualTo(1);
        assertThat(AccountType.CARD_NETWORK_CLEARING.signFor(Direction.CREDIT)).isEqualTo(-1);
    }

    @Test
    @DisplayName("liabilities and revenue increase on the credit side")
    void liabilitiesIncreaseOnCredit() {
        assertThat(AccountType.MERCHANT_PAYABLE.increasesOn()).isEqualTo(Direction.CREDIT);
        assertThat(AccountType.MERCHANT_PAYABLE.signFor(Direction.CREDIT)).isEqualTo(1);
        assertThat(AccountType.PLATFORM_FEE_REVENUE.signFor(Direction.CREDIT)).isEqualTo(1);
    }

    @Test
    @DisplayName("only the merchant payable account is per-merchant")
    void onlyPayableIsPerMerchant() {
        assertThat(AccountType.MERCHANT_PAYABLE.isPerMerchant()).isTrue();
        assertThat(AccountType.PLATFORM_FEE_REVENUE.isPerMerchant()).isFalse();
        assertThat(AccountType.CARD_NETWORK_CLEARING.isPerMerchant()).isFalse();
    }

    @Test
    void directionsAreOpposites() {
        assertThat(Direction.DEBIT.opposite()).isEqualTo(Direction.CREDIT);
        assertThat(Direction.CREDIT.opposite()).isEqualTo(Direction.DEBIT);
    }
}
