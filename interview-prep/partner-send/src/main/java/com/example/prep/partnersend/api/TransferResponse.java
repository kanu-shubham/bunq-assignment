package com.example.prep.partnersend.api;

import com.example.prep.partnersend.domain.Transfer;

public record TransferResponse(
        String id,
        String partnerReference,
        String amountMinorUnits,
        String currency,
        String status,
        String schemeReference,
        String createdAt) {

    public static TransferResponse from(Transfer transfer) {
        return new TransferResponse(
                transfer.getId(),
                transfer.getPartnerReference(),
                Long.toString(transfer.getAmount().minorUnits()),
                transfer.getAmount().currency().getCurrencyCode(),
                transfer.getStatus().name(),
                transfer.getSchemeReference(),
                transfer.getCreatedAt().toString());
    }
}
