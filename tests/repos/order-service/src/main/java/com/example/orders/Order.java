package com.example.orders;

/**
 * Aggregate root for a customer order.
 *
 * <p>Carries its own state transitions so the call graph can resolve these
 * domain methods (``validate``, ``markPaid``, ``markFulfilled``) to concrete
 * definitions in this repo.
 */
public class Order {

    private String id;
    private String productId;
    private int quantity;
    private String customerEmail;
    private String status;
    private int totalCents;

    public Order(String productId, int quantity, String customerEmail) {
        this.productId = productId;
        this.quantity = quantity;
        this.customerEmail = customerEmail;
        this.status = "PENDING";
    }

    public void validate() {
        if (quantity <= 0) {
            throw new IllegalArgumentException("Quantity must be positive");
        }
        if (customerEmail == null || customerEmail.isBlank()) {
            throw new IllegalArgumentException("Customer email is required");
        }
    }

    public void markPaid() {
        this.status = "PAID";
    }

    public void markFulfilled() {
        this.status = "FULFILLED";
    }

    public void setTotalCents(int totalCents) {
        this.totalCents = totalCents;
    }

    public String getId() { return id; }
    public String getProductId() { return productId; }
    public int getQuantity() { return quantity; }
    public String getCustomerEmail() { return customerEmail; }
    public String getStatus() { return status; }
    public int getTotalCents() { return totalCents; }
}
