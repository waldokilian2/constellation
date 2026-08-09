package com.example.fulfillment;

import org.springframework.stereotype.Service;

/**
 * Application service for the fulfillment domain. Consumed by message handlers
 * and exposed via REST; fans out to the repository, the shipment event
 * producer (Kafka "shipment-events"), and a synchronous HTTP call back to
 * order-service (bidirectional HTTP edge).
 */
@Service
public class FulfillmentService {

    private final ShipmentRepository shipmentRepository;
    private final ShipmentEventProducer eventProducer;
    private final OrderClient orderClient;

    public FulfillmentService(ShipmentRepository shipmentRepository,
                              ShipmentEventProducer eventProducer,
                              OrderClient orderClient) {
        this.shipmentRepository = shipmentRepository;
        this.eventProducer = eventProducer;
        this.orderClient = orderClient;
    }

    public Shipment createShipment(String orderId) {
        Shipment shipment = new Shipment(orderId);
        shipment.schedule();
        shipmentRepository.save(shipment);
        eventProducer.publishCreated(shipment);
        return shipment;
    }

    public Shipment releaseShipment(String orderId) {
        Shipment shipment = shipmentRepository.findByOrderId(orderId);
        if (shipment == null) {
            shipment = createShipment(orderId);
        }
        shipment.release();
        shipmentRepository.save(shipment);
        eventProducer.publishReady(shipment);
        orderClient.notifyOrderFulfilled(orderId);
        return shipment;
    }

    public Shipment getStatus(String orderId) {
        return shipmentRepository.findByOrderId(orderId);
    }
}
