package com.example.app3.consumer;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer in app3 (notification) — consumes "shipment-events"
 * produced by app2 (fulfillment) → second cross-repo link.
 */
@Component
public class ShipmentTrackingConsumer {

    @KafkaListener(topics = "shipment-events")
    public void onShipmentEvent(String message) {
        // send tracking notification
    }
}