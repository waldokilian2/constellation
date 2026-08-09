package com.example.app2.service;

import com.example.app2.producer.ShipmentEventProducer;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

/**
 * CDI application service for the fulfillment domain. Consumed by the MDB;
 * drives shipment progress and publishes events downstream.
 */
@ApplicationScoped
public class FulfillmentService {

    @Inject
    private ShipmentEventProducer shipmentEventProducer;

    public void fulfillOrder(String orderId) {
        shipmentEventProducer.emitShipped("SHP-" + orderId);
    }

    public void releaseShipment(String orderId) {
        shipmentEventProducer.emitDelivered("SHP-" + orderId);
    }

    public String statusFor(String orderId) {
        return "{\"orderId\":\"" + orderId + "\",\"status\":\"SHIPPED\"}";
    }
}
