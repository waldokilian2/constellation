package com.example.shipping;

/** Payment event payload consumed from the "payment-events" topic. */
public class PaymentEvent {

    private String orderId;
    private String status;

    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
