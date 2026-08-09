package com.example.fulfillment;

/** Message payload received from the "order-events" queue. */
public class OrderEvent {

    private final String orderId;
    private final String type;

    public OrderEvent(String orderId, String type) {
        this.orderId = orderId;
        this.type = type;
    }

    public String getOrderId() { return orderId; }
    public String getType() { return type; }
}
