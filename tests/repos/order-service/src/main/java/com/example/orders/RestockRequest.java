package com.example.orders;

/** Payload for a restock request (binds the POST /api/restock body). */
public class RestockRequest {

    private String productId;
    private int units;

    public String getProductId() {
        return productId;
    }

    public void setProductId(String productId) {
        this.productId = productId;
    }

    public int getUnits() {
        return units;
    }

    public void setUnits(int units) {
        this.units = units;
    }
}
