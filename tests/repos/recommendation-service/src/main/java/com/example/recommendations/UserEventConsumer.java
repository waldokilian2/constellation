package com.example.recommendations;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "user-events" topic.
 *
 * <p>Cross-repo link: user-service publishes "user-events" (see
 * {@code UserEventPublisher}); this consumer establishes the
 * user &rarr; recommendation edge.
 */
@Component
public class UserEventConsumer {

    private final RecommendationService recommendationService;

    public UserEventConsumer(RecommendationService recommendationService) {
        this.recommendationService = recommendationService;
    }

    @KafkaListener(topics = "user-events")
    public void onUserEvent(UserEvent event) {
        recommendationService.recordActivity(event.getUserId(), event.getProductId());
    }
}
