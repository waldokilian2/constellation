package com.example.inventory;

/** Order summary returned by the order-service API. */
public class OrderSummary {

    private String id;
    private String sku;

    public String getId() { return id; }
    public String getSku() { return sku; }
}
