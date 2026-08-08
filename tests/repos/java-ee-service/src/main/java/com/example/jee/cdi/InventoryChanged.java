package com.example.jee.cdi;

/** Event fired by producers when inventory stock changes. */
public class InventoryChanged {
    private String sku;
    private int newStock;

    public String getSku() { return sku; }
    public int getNewStock() { return newStock; }
}
