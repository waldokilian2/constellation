package com.example.analytics;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes analytics events to the "analytics-events" Kafka topic.
 *
 * <p>Consumed by order-service ({@code AnalyticsEventConsumer}), this edge
 * closes the order &lt;-&gt; analytics dependency cycle that {@code find_cycles}
 * surfaces. Detected as a {@code kafka-producer} by the field type
 * (KafkaTemplate) + {@code send} method.
 */
@Service
public class AnalyticsEventProducer {

    private final KafkaTemplate<String, Object> kafkaTemplate;

    public AnalyticsEventProducer(KafkaTemplate<String, Object> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publish(String orderId, MetricSnapshot snapshot) {
        kafkaTemplate.send("analytics-events",
            new AnalyticsEvent(orderId, snapshot.getMetric(), snapshot.getValue()));
    }
}
