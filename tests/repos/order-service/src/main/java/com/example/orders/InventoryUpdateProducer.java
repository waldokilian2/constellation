package com.example.orders;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes inventory adjustments to the "inventory-updates" Kafka topic.
 *
 * <p>Consumed by {@link InventoryUpdateConsumer} (same repo), closing the
 * previously-orphaned "inventory-updates" channel. Detected as a
 * {@code kafka-producer} by the field type (KafkaTemplate) + {@code send}.
 */
@Service
public class InventoryUpdateProducer {

    private final KafkaTemplate<String, Object> kafkaTemplate;

    public InventoryUpdateProducer(KafkaTemplate<String, Object> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    public void publishRestock(String productId, int restockedUnits) {
        kafkaTemplate.send("inventory-updates",
            new InventoryUpdateEvent(productId, restockedUnits));
    }
}
