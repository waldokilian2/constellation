package com.example.notification;

/** Message payload received from the "shipment-events" topic. */
public class ShipmentEvent {

    private final String orderId;
    private final String type;

    public ShipmentEvent(String orderId, String type) {
        this.orderId = orderId;
        this.type = type;
    }

    public String getOrderId() { return orderId; }
    public String getType() { return type; }
}
