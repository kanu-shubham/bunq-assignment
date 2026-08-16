package com.paykit.payment.web;

import com.paykit.common.error.Exceptions;
import com.paykit.common.money.Money;
import com.paykit.payment.domain.Charge;
import com.paykit.payment.idempotency.Idempotent;
import com.paykit.payment.repository.ChargeRepository;
import com.paykit.payment.service.RefundService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@Tag(name = "Refunds and Charges", description = "Send money back and inspect captures")
public class RefundController {

    private final RefundService refundService;
    private final ChargeRepository chargeRepository;

    public RefundController(RefundService refundService, ChargeRepository chargeRepository) {
        this.refundService = refundService;
        this.chargeRepository = chargeRepository;
    }

    @PostMapping("/v1/refunds")
    @ResponseStatus(HttpStatus.CREATED)
    @Idempotent(value = "create_refund", required = true)
    @Operation(summary = "Refund a charge, fully or partially")
    public PaymentDtos.RefundResponse create(
            @RequestHeader("X-Merchant-Id") String merchantId,
            @Valid @RequestBody PaymentDtos.CreateRefundRequest request) {

        Charge charge = chargeRepository.findByIdAndMerchantId(request.charge(), merchantId)
                .orElseThrow(() -> new Exceptions.NotFoundException("charge", request.charge()));

        Money amount = request.amount() == null
                ? null
                : Money.of(request.amount(), charge.getCurrency());

        return PaymentDtos.RefundResponse.from(
                refundService.refund(merchantId, charge.getId(), amount, request.reason()));
    }

    @GetMapping("/v1/refunds/{refundId}")
    public PaymentDtos.RefundResponse get(@RequestHeader("X-Merchant-Id") String merchantId,
                                          @PathVariable String refundId) {
        return PaymentDtos.RefundResponse.from(refundService.require(merchantId, refundId));
    }

    @GetMapping("/v1/charges/{chargeId}")
    public PaymentDtos.ChargeResponse getCharge(@RequestHeader("X-Merchant-Id") String merchantId,
                                                @PathVariable String chargeId) {
        return PaymentDtos.ChargeResponse.from(
                chargeRepository.findByIdAndMerchantId(chargeId, merchantId)
                        .orElseThrow(() -> new Exceptions.NotFoundException("charge", chargeId)));
    }

    @GetMapping("/v1/charges/{chargeId}/refunds")
    public PaymentDtos.ListResponse<PaymentDtos.RefundResponse> listRefunds(
            @RequestHeader("X-Merchant-Id") String merchantId,
            @PathVariable String chargeId) {

        List<PaymentDtos.RefundResponse> refunds = refundService.listForCharge(merchantId, chargeId).stream()
                .map(PaymentDtos.RefundResponse::from)
                .toList();

        return PaymentDtos.ListResponse.of(refunds, false, refunds.size());
    }
}
