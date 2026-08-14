package com.example.users;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "shipment-events" topic.
 *
 * <p>Cross-repo link: shipping-service publishes "shipment-events" (see
 * {@code ShipmentEventProducer}); this consumer establishes the
 * shipping &rarr; user edge. Emails the customer when their order ships.
 */
@Component
public class ShipmentNotifier {

    private final UserService userService;

    public ShipmentNotifier(UserService userService) {
        this.userService = userService;
    }

    @KafkaListener(topics = "shipment-events")
    public void onShipmentEvent(ShipmentEvent event) {
        userService.notifyShipment(event.getOrderId());
    }
}
