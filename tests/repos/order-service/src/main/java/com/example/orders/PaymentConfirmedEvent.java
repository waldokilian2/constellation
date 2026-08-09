package com.example.orders;

/** Domain event emitted once a payment for an order is confirmed. */
public class PaymentConfirmedEvent {

    private final String orderId;

    public PaymentConfirmedEvent(String orderId) {
        this.orderId = orderId;
    }

    public String getOrderId() { return orderId; }
}
