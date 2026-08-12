package com.example.orders;

import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST entry point representing a payment-gateway confirmation callback.
 *
 * <p>Wires the previously-orphaned {@link PaymentEventListener}: this endpoint
 * publishes a {@link PaymentConfirmedEvent} via {@link PaymentEventPublisher},
 * giving the event-listener a producer so {@code find_orphans} no longer flags
 * it.
 */
@RestController
public class PaymentController {

    private final PaymentEventPublisher paymentEventPublisher;

    public PaymentController(PaymentEventPublisher paymentEventPublisher) {
        this.paymentEventPublisher = paymentEventPublisher;
    }

    @PostMapping("/api/payments/{orderId}/confirmed")
    public String confirm(@PathVariable("orderId") String orderId) {
        paymentEventPublisher.paymentConfirmed(orderId);
        return "{\"confirmed\":true}";
    }
}
