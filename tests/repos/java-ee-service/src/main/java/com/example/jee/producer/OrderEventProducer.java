package com.example.jee.producer;

import com.example.jee.config.Channels;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

/**
 * Producer detection by declared field type + channel resolution:
 *  - KafkaTemplate field → kafka-producer
 *  - literal channel ("inventory-updates")
 *  - constant channel (Channels.NOTIFICATIONS)
 *  - ${...} placeholder channel resolved from application.properties
 *  - array topics in a listener → one endpoint per element
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

    public void emitInventoryUpdate(String sku) {
        kafkaTemplate.send(Channels.INVENTORY_UPDATES, sku);
    }

    public void emitNotification(String message) {
        kafkaTemplate.send("${app.topic.notify}", message);
    }
}
