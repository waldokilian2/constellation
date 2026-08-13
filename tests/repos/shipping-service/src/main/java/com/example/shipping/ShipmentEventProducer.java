package com.example.shipping;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes shipment lifecycle events on the "shipment-events" channel.
 *
 * <p>Cross-repo link: notification-service (see {@code ShipmentTrackingConsumer})
 * and user-service (see {@code ShipmentNotifier}) consume "shipment-events",
 * establishing the shipping &rarr; notification and shipping &rarr; user edges.
 */
@Service
public class ShipmentEventProducer {

    private final RabbitTemplate rabbitTemplate;

    public ShipmentEventProducer(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    public void publishShipped(Shipment shipment) {
        rabbitTemplate.convertAndSend("shipment-events",
                new ShipmentEvent(shipment.getOrderId(), "SHIPPED"));
    }
}
