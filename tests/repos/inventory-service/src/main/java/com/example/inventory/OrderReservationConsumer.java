package com.example.inventory;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "order-events" topic.
 *
 * <p>Cross-repo link: order-service publishes "order-events"; this consumer
 * establishes the order &rarr; inventory edge. Reserves stock for the order.
 */
@Component
public class OrderReservationConsumer {

    private final InventoryService inventoryService;

    public OrderReservationConsumer(InventoryService inventoryService) {
        this.inventoryService = inventoryService;
    }

    @KafkaListener(topics = "order-events")
    public void onOrderEvent(OrderEvent event) {
        inventoryService.reserve(event.getOrderId());
    }
}
