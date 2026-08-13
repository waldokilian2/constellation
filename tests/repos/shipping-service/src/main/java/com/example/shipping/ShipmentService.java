package com.example.shipping;

import org.springframework.stereotype.Service;

/** Core shipment logic: assigns a carrier and publishes shipment events. */
@Service
public class ShipmentService {

    private final ShipmentEventProducer eventProducer;

    public ShipmentService(ShipmentEventProducer eventProducer) {
        this.eventProducer = eventProducer;
    }

    public Shipment ship(String orderId) {
        Shipment shipment = new Shipment(orderId, "STANDARD");
        eventProducer.publishShipped(shipment);
        return shipment;
    }

    public Shipment assign(String orderId, String carrier) {
        return new Shipment(orderId, carrier);
    }

    public Shipment findByOrder(String orderId) {
        return new Shipment(orderId, "STANDARD");
    }
}
