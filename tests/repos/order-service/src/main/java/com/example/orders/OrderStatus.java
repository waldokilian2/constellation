package com.example.orders;

/** Composite view of an order's own status plus its fulfillment status. */
public class OrderStatus {

    private final String orderStatus;
    private final String fulfillmentStatus;

    public OrderStatus(String orderStatus, String fulfillmentStatus) {
        this.orderStatus = orderStatus;
        this.fulfillmentStatus = fulfillmentStatus;
    }

    public String getOrderStatus() { return orderStatus; }
    public String getFulfillmentStatus() { return fulfillmentStatus; }
}
