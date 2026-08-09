package com.example.app2.producer;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

/**
 * Producer in app2 (fulfillment) — emits shipment progress to
 * "shipment-events". Consumed by app3 (notification service) for tracking
 * updates → second cross-repo link.
 */
@Service
public class ShipmentEventProducer {

    private final KafkaTemplate<String, String> kafkaTemplate;

    public ShipmentEventProducer(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void emitShipped(String shipmentId) {
        kafkaTemplate.send("shipment-events", shipmentId);
    }

    public void emitDelivered(String shipmentId) {
        kafkaTemplate.send("shipment-events", shipmentId);
    }
}
