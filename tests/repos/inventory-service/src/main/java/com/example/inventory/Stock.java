package com.example.inventory;

/** Current stock level for a product SKU. */
public class Stock {

    private final String sku;
    private final int stockLevel;

    public Stock(String sku, int stockLevel) {
        this.sku = sku;
        this.stockLevel = stockLevel;
    }

    public String getSku() { return sku; }
    public int getStockLevel() { return stockLevel; }
}
