package com.example.app2.consumer;

import com.example.app2.service.FulfillmentService;

import jakarta.inject.Inject;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer in app2 with an array of topics — one entry point per
 * element ("fulfillment-commands" + "shipment-events").
 *
 * <p>Delegates to the {@link FulfillmentService} so each entry point resolves
 * a real (EXTRACTED) call tree instead of being an empty stub.
 */
@Component
public class FulfillmentCommandConsumer {

    @Inject
    private FulfillmentService fulfillmentService;

    @KafkaListener(topics = {"fulfillment-commands", "shipment-events"})
    public void onCommand(String message) {
        fulfillmentService.fulfillOrder(message);
    }
}
