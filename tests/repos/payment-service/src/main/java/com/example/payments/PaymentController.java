package com.example.payments;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST entry points for payment management.
 */
@RestController
@RequestMapping("/api/payments")
public class PaymentController {

    private final PaymentService paymentService;

    public PaymentController(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    @PostMapping("/charge")
    public Payment charge(@RequestBody ChargeRequest request) {
        return paymentService.charge(request.getOrderId());
    }

    @GetMapping("/{orderId}")
    public Payment getPayment(@PathVariable("orderId") String orderId) {
        return paymentService.findByOrder(orderId);
    }
}
