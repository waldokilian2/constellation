package com.example.recommendations;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "order-events" topic.
 *
 * <p>Cross-repo link: order-service publishes "order-events" (see
 * {@code OrderEventProducer}); this consumer establishes the
 * order &rarr; recommendation edge. Feeds the recommendation model.
 */
@Component
public class OrderEventConsumer {

    private final RecommendationService recommendationService;

    public OrderEventConsumer(RecommendationService recommendationService) {
        this.recommendationService = recommendationService;
    }

    @KafkaListener(topics = "order-events")
    public void onOrderEvent(OrderEvent event) {
        recommendationService.ingestOrder(event.getOrderId());
    }
}
