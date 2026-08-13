package com.example.payments;

/** Inbound charge request body. */
public class ChargeRequest {

    private String orderId;

    public String getOrderId() { return orderId; }
    public void setOrderId(String orderId) { this.orderId = orderId; }
}
