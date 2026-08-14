package com.example.recommendations;

/** User activity event payload consumed from the "user-events" topic. */
public class UserEvent {

    private String userId;
    private String action;
    private String productId;

    public String getUserId() { return userId; }
    public String getAction() { return action; }
    public String getProductId() { return productId; }
}
