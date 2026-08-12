package com.example.orders;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "analytics-events" topic.
 *
 * <p>Cross-repo link: analytics-service produces "analytics-events" (see
 * {@code AnalyticsEventProducer}); this consumer establishes the
 * analytics &rarr; order edge. Together with order-service's existing
 * "order-events" producer (consumed by analytics), this closes the
 * order &lt;-&gt; analytics dependency cycle that {@code find_cycles} reports.
 */
@Component
public class AnalyticsEventConsumer {

    private final OrderService orderService;

    public AnalyticsEventConsumer(OrderService orderService) {
        this.orderService = orderService;
    }

    @KafkaListener(topics = "analytics-events")
    public void onAnalyticsEvent(AnalyticsEvent event) {
        orderService.getOrder(event.getOrderId());
    }
}
