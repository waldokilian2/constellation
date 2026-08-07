package com.example.fulfillment;

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

    public String getId() { return id; }
    public String getOrderId() { return orderId; }
    public String getStatus() { return status; }
}
