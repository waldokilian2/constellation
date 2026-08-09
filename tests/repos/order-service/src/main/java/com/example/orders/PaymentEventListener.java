package com.example.orders;

import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/**
 * In-process event listener — entry point via {@code @EventListener}.
 * Listens for a payment-confirmed event and confirms the order as paid.
 */
@Component
public class PaymentEventListener {

    private final OrderService orderService;

    public PaymentEventListener(OrderService orderService) {
        this.orderService = orderService;
    }

    @EventListener
    public void handlePaymentConfirmed(PaymentConfirmedEvent event) {
        orderService.confirmPaid(event.getOrderId());
    }
}
