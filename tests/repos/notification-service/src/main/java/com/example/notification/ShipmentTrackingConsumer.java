package com.example.notification;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer — entry point on the "shipment-events" topic.
 * Produced by fulfillment-service; dispatches tracking notifications.
 */
@Component
public class ShipmentTrackingConsumer {

    private final NotificationService notificationService;

    public ShipmentTrackingConsumer(NotificationService notificationService) {
        this.notificationService = notificationService;
    }

    @KafkaListener(topics = "shipment-events")
    public void handleShipmentEvent(ShipmentEvent event) {
        if ("CREATED".equals(event.getType())) {
            notificationService.notifyShipmentCreated(event.getOrderId());
        } else if ("READY".equals(event.getType())) {
            notificationService.notifyShipmentReady(event.getOrderId());
        }
    }
}
