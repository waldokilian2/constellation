package com.example.users;

/** Recommendation event payload consumed from the "recommendation-events" topic. */
public class RecommendationEvent {

    private String userId;
    private String productId;

    public String getUserId() { return userId; }
    public String getProductId() { return productId; }
}
