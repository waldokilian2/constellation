package com.example.app2.mdb;

import com.example.app2.service.FulfillmentService;

import javax.ejb.ActivationConfigProperty;
import javax.ejb.MessageDriven;
import javax.ejb.MessageListener;
import javax.inject.Inject;
import javax.jms.Message;
import javax.jms.TextMessage;

/**
 * JMS MessageDriven Bean in app2 (fulfillment) — consumes "order-events"
 * produced by app1 (order service) → cross-repo link. Delegates to the
 * {@link FulfillmentService} so the call graph resolves a deep EXTRACTED tree.
 */
@MessageDriven(activationConfig = {
    @ActivationConfigProperty(propertyName = "destinationType", propertyValue = "javax.jms.Queue"),
    @ActivationConfigProperty(propertyName = "destination", propertyValue = "order-events")
})
public class OrderEventsMdb implements MessageListener {

    @Inject
    private FulfillmentService fulfillmentService;

    @Override
    public void onMessage(Message message) {
        try {
            String orderId = (message instanceof TextMessage)
                ? ((TextMessage) message).getText()
                : "ORD-UNKNOWN";
            fulfillmentService.fulfillOrder(orderId);
        } catch (Exception e) {
            // swallow — demo fixture
        }
    }
}
