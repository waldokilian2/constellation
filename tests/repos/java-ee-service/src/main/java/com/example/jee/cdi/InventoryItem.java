package com.example.jee.cdi;

/** Placeholder event type observed by InventoryObserver. */
public class InventoryItem {
    private String sku;
    private int stock;

    public String getSku() { return sku; }
    public int getStock() { return stock; }
}