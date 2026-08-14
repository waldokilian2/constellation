package com.example.shipping;

/** Shipment lifecycle event published on the "shipment-events" channel. */
public class ShipmentEvent {

    private final String orderId;
    private final String status;

    public ShipmentEvent(String orderId, String status) {
        this.orderId = orderId;
        this.status = status;
    }

    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
