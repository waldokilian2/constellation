package com.example.payments;

/** Payment lifecycle event published on the "payment-events" channel. */
public class PaymentEvent {

    private final String orderId;
    private final String status;

    public PaymentEvent(String orderId, String status) {
        this.orderId = orderId;
        this.status = status;
    }

    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
