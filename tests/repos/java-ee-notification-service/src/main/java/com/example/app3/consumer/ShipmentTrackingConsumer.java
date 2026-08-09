package com.example.app3.consumer;

import com.example.app3.service.NotificationService;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import jakarta.inject.Inject;

/**
 * Kafka consumer in app3 (notification) — consumes "shipment-events"
 * produced by app2 (fulfillment) → second cross-repo link. Delegates to the
 * {@link NotificationService} for a resolvable call tree.
 */
@Component
public class ShipmentTrackingConsumer {

    @Inject
    private NotificationService notificationService;

    @KafkaListener(topics = "shipment-events")
    public void onShipmentEvent(String message) {
        notificationService.notifyShipmentUpdate(message);
    }
}
