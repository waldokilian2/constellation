package com.example.fulfillment;

import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

@Service
public class FulfillmentStatusController {

    private final ShipmentRepository shipmentRepository;

    public FulfillmentStatusController(ShipmentRepository shipmentRepository,
                                       KafkaTemplate<String, Object> kafkaTemplate) {
        this.shipmentRepository = shipmentRepository;
    }

    @GetMapping("/api/fulfillment/status/{orderId}")
    public Shipment getFulfillmentStatus(@PathVariable String orderId) {
        return shipmentRepository.findByOrderId(orderId);
    }
}