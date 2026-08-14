package com.example.users;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "recommendation-events" topic.
 *
 * <p>Cross-repo link: recommendation-service publishes
 * "recommendation-events" (see {@code RecommendationEventProducer}); this
 * consumer establishes the recommendation &rarr; user edge. Together with
 * user-service's "user-events" producer (consumed by recommendation-service),
 * this closes the user &harr; recommendation dependency cycle.
 */
@Component
public class RecommendationConsumer {

    private final UserService userService;

    public RecommendationConsumer(UserService userService) {
        this.userService = userService;
    }

    @KafkaListener(topics = "recommendation-events")
    public void onRecommendationEvent(RecommendationEvent event) {
        userService.storeRecommendation(event.getUserId(), event.getProductId());
    }
}
