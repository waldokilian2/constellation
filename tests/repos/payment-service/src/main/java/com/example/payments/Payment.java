package com.example.payments;

/** A recorded payment for an order. */
public class Payment {

    private final String orderId;
    private final String status;

    public Payment(String orderId, String status) {
        this.orderId = orderId;
        this.status = status;
    }

    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
