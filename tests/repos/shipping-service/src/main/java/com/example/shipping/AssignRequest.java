package com.example.shipping;

/** Inbound carrier assignment request body. */
public class AssignRequest {

    private String orderId;
    private String carrier;

    public String getOrderId() { return orderId; }
    public void setOrderId(String orderId) { this.orderId = orderId; }
    public String getCarrier() { return carrier; }
    public void setCarrier(String carrier) { this.carrier = carrier; }
}
