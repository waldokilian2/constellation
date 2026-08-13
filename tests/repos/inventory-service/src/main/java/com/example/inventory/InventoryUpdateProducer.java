package com.example.inventory;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes stock updates on the "inventory-updates" topic.
 *
 * <p>Cross-repo link: order-service consumes "inventory-updates" (see
 * {@code InventoryUpdateConsumer}); this producer establishes the
 * inventory &rarr; order edge, closing the order &harr; inventory cycle.
 */
@Service
public class InventoryUpdateProducer {

    private final KafkaTemplate<String, InventoryUpdateEvent> kafkaTemplate;

    public InventoryUpdateProducer(KafkaTemplate<String, InventoryUpdateEvent> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishUpdate(String productId, int stockLevel) {
        kafkaTemplate.send("inventory-updates",
                new InventoryUpdateEvent(productId, stockLevel));
    }
}
