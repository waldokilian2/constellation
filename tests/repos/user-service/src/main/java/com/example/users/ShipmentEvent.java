package com.example.users;

/** Shipment event payload consumed from the "shipment-events" topic. */
public class ShipmentEvent {

    private String orderId;
    private String status;

    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
