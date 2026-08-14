package com.example.shipping;

/** A shipment bound to an order and a carrier. */
public class Shipment {

    private final String orderId;
    private final String carrier;

    public Shipment(String orderId, String carrier) {
        this.orderId = orderId;
        this.carrier = carrier;
    }

    public String getOrderId() { return orderId; }
    public String getCarrier() { return carrier; }
}
