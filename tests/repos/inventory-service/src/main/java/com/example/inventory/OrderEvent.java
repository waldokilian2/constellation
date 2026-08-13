package com.example.inventory;

/** Order lifecycle event payload consumed from the "order-events" topic. */
public class OrderEvent {

    private String orderId;
    private String status;

    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
