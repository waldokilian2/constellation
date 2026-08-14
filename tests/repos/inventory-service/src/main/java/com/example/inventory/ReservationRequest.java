package com.example.inventory;

/** Inbound reservation request body. */
public class ReservationRequest {

    private String orderId;

    public String getOrderId() { return orderId; }
    public void setOrderId(String orderId) { this.orderId = orderId; }
}
