package com.example.app3.cdi;

/** Event observed by InventoryObserver. */
public class InventoryChanged {
    private String sku;
    private int newStock;

    public String getSku() { return sku; }
    public int getNewStock() { return newStock; }
}