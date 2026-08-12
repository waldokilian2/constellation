package com.example.analytics;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "order-events" topic.
 *
 * <p>Cross-repo link: order-service produces "order-events"; this consumer
 * (plus the Camel route) establishes the order &rarr; analytics edge. Combined
 * with {@link AnalyticsEventProducer} (&rarr; "analytics-events", consumed by
 * order-service) it closes the order &lt;-&gt; analytics cycle that
 * {@code find_cycles} reports.
 */
@Component
public class OrderAnalyticsConsumer {

    private final AnalyticsService analyticsService;

    public OrderAnalyticsConsumer(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @KafkaListener(topics = "order-events")
    public void onOrderEvent(OrderEvent event) {
        analyticsService.record(event.getOrderId(), "order." + event.getType(), 1L);
    }
}
