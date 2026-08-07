package com.example.orders;

public class Order {
    private String id;
    private String productId;
    private int quantity;
    private String status;
    private boolean backordered;

    public Order(String productId, int quantity) {
        this.productId = productId;
        this.quantity = quantity;
        this.status = "PENDING";
    }

    public void validate() {
        if (quantity <= 0) {
            throw new IllegalArgumentException("Quantity must be positive");
        }
    }

    public void markAsPaid() {
        this.status = "PAID";
    }

    public String getId() { return id; }
    public String getProductId() { return productId; }
    public int getQuantity() { return quantity; }
    public String getStatus() { return status; }
    public boolean isBackordered() { return backordered; }
    public void setBackordered(boolean backordered) { this.backordered = backordered; }
}
