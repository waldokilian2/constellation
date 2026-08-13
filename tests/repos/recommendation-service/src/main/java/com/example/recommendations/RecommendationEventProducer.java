package com.example.recommendations;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes product recommendations on the "recommendation-events" topic.
 *
 * <p>Cross-repo link: user-service consumes "recommendation-events" (see
 * {@code RecommendationConsumer}); this producer establishes the
 * recommendation &rarr; user edge.
 */
@Service
public class RecommendationEventProducer {

    private final KafkaTemplate<String, RecommendationEvent> kafkaTemplate;

    public RecommendationEventProducer(KafkaTemplate<String, RecommendationEvent> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishRecommendation(String userId, String productId) {
        kafkaTemplate.send("recommendation-events",
                new RecommendationEvent(userId, productId));
    }
}
