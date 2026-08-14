package com.example.recommendations;

import org.springframework.stereotype.Service;

/** Core recommendation logic fed by order and user activity streams. */
@Service
public class RecommendationService {

    private final RecommendationEventProducer eventProducer;

    public RecommendationService(RecommendationEventProducer eventProducer) {
        this.eventProducer = eventProducer;
    }

    public void ingestOrder(String orderId) {
        // update the model with the new order
    }

    public void recordActivity(String userId, String productId) {
        eventProducer.publishRecommendation(userId, productId);
    }

    public Recommendation forUser(String userId) {
        return new Recommendation(userId, "SKU-42");
    }
}
