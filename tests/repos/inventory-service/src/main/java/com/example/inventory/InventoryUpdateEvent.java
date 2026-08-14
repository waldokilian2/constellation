package com.example.inventory;

/** Stock update event published on the "inventory-updates" topic. */
public class InventoryUpdateEvent {

    private final String productId;
    private final int stockLevel;

    public InventoryUpdateEvent(String productId, int stockLevel) {
        this.productId = productId;
        this.stockLevel = stockLevel;
    }

    public String getProductId() { return productId; }
    public int getStockLevel() { return stockLevel; }
}
