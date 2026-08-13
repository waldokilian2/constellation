package com.example.payments;

/** Response payload returned by the fulfillment status endpoint. */
public class FulfillmentStatus {

    private String orderId;
    private String status;

    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
