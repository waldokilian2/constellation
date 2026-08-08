package com.example.app2.consumer;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer in app2 with an array of topics — one entry point per
 * element ("fulfillment-commands" + "shipment-events").
 */
@Component
public class FulfillmentCommandConsumer {

    @KafkaListener(topics = {"fulfillment-commands", "shipment-events"})
    public void onCommand(String message) {
        // dispatch to warehouse
    }
}