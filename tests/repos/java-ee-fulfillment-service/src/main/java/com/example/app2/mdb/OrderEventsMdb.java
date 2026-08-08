package com.example.app2.mdb;

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

    @Override
    public void onMessage(Message message) {
        // fulfill the order
    }
}