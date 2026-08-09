package com.example.orders;

/** Inbound payload for creating an order. */
public class OrderRequest {

    private String productId;
    private int quantity;
    private String customerEmail;

    public OrderRequest() {
    }

    public OrderRequest(String productId, int quantity, String customerEmail) {
        this.productId = productId;
        this.quantity = quantity;
        this.customerEmail = customerEmail;
    }

    public String getProductId() { return productId; }
    public int getQuantity() { return quantity; }
    public String getCustomerEmail() { return customerEmail; }
}
