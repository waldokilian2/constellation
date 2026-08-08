package com.example.app2.mdb;

import com.example.app2.producer.ShipmentEventProducer;

import javax.jms.Message;
import javax.jms.MessageListener;
import javax.ejb.MessageDriven;
import javax.ejb.ActivationConfigProperty;

/**
 * JMS MessageDriven Bean in app2 (fulfillment) — consumes "order-events"
 * produced by app1 (order service) → cross-repo link.
 */
@MessageDriven(activationConfig = {
    @ActivationConfigProperty(propertyName = "destinationType", propertyValue = "javax.jms.Queue"),
    @ActivationConfigProperty(propertyName = "destination", propertyValue = "order-events")
})
public class OrderEventsMdb implements MessageListener {

    private final ShipmentEventProducer shipmentEventProducer;

    public OrderEventsMdb(ShipmentEventProducer shipmentEventProducer) {
        this.shipmentEventProducer = shipmentEventProducer;
    }

    @Override
    public void onMessage(Message message) {
        // fulfill the order, then publish progress downstream
        shipmentEventProducer.emitShipped("SHP-1");
    }
}