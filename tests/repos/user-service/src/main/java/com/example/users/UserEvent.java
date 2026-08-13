package com.example.users;

/** User activity event published on the "user-events" channel. */
public class UserEvent {

    private final String userId;
    private final String action;
    private final String productId;

    public UserEvent(String userId, String action, String productId) {
        this.userId = userId;
        this.action = action;
        this.productId = productId;
    }

    public String getUserId() { return userId; }
    public String getAction() { return action; }
    public String getProductId() { return productId; }
}
