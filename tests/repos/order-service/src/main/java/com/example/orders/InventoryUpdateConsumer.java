package com.example.orders;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer — entry point on the "inventory-updates" topic.
 * Backorders every open order for a product once its stock hits zero.
 */
@Component
public class InventoryUpdateConsumer {

    private final OrderRepository orderRepository;

    public InventoryUpdateConsumer(OrderRepository orderRepository) {
        this.orderRepository = orderRepository;
    }

    @KafkaListener(topics = "inventory-updates")
    public void onInventoryUpdate(InventoryUpdateEvent event) {
        if (event.getStockLevel() == 0) {
            for (Order order : orderRepository.findByProduct(event.getProductId())) {
                orderRepository.save(order);
            }
        }
    }
}
