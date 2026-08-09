package com.example.fulfillment;

/** Shipment aggregate — lifecycle transitions that resolve in the call graph. */
public class Shipment {

    private String id;
    private String orderId;
    private String status;

    public Shipment(String orderId) {
        this.orderId = orderId;
        this.status = "CREATED";
    }

    public void schedule() {
        this.status = "SCHEDULED";
    }

    public void release() {
        this.status = "READY";
    }

    public void markDelivered() {
        this.status = "DELIVERED";
    }

    public String getId() { return id; }
    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
