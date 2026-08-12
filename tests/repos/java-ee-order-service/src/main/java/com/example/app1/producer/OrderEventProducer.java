package com.example.app1.producer;

import com.example.app1.config.Channels;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

/**
 * Producers in app1 (order service) — all resolve to the "order-events"
 * channel:
 *  - literal        → "order-events"
 *  - constant       → Channels.ORDER_EVENTS
 *  - ${...}         → from application.properties
 *
 * The consumer of "order-events" lives in app2's MDB, giving a cross-repo link.
 */
@Service
public class OrderEventProducer {

    private final KafkaTemplate<String, String> kafkaTemplate;

    public OrderEventProducer(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void emitOrderPlaced(String orderId) {
        kafkaTemplate.send("order-events", orderId);
    }

    public void emitOrderUpdated(String orderId) {
        kafkaTemplate.send(Channels.ORDER_EVENTS, orderId);
    }

    public void emitOrderCancelled(String orderId) {
        kafkaTemplate.send("${app.topic.orders}", orderId);
    }

    public void emitFulfillmentCommand(String orderId) {
        kafkaTemplate.send("fulfillment-commands", orderId);
    }
}
