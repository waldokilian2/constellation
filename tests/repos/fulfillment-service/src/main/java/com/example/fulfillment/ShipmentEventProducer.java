package com.example.fulfillment;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes shipment progress to the Kafka "shipment-events" topic.
 * Consumers in notification-service pick these up (cross-repo link).
 */
@Service
public class ShipmentEventProducer {

    private final KafkaTemplate<String, Object> kafkaTemplate;

    public ShipmentEventProducer(KafkaTemplate<String, Object> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishCreated(Shipment shipment) {
        kafkaTemplate.send("shipment-events", new ShipmentEvent(shipment.getOrderId(), "CREATED"));
    }

    public void publishReady(Shipment shipment) {
        kafkaTemplate.send("shipment-events", new ShipmentEvent(shipment.getOrderId(), "READY"));
    }
}
