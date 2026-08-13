package com.example.recommendations;

/** Recommendation event published on the "recommendation-events" topic. */
public class RecommendationEvent {

    private final String userId;
    private final String productId;

    public RecommendationEvent(String userId, String productId) {
        this.userId = userId;
        this.productId = productId;
    }

    public String getUserId() { return userId; }
    public String getProductId() { return productId; }
}
