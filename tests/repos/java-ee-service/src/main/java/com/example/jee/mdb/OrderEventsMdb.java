package com.example.jee.mdb;

import javax.jms.Message;
import javax.jms.MessageListener;
import javax.ejb.MessageDriven;
import javax.ejb.ActivationConfigProperty;

/**
 * JMS MessageDriven Bean — exercises the MDB entry point with the
 * activationConfig destination resolved to a channel.
 */
@MessageDriven(activationConfig = {
    @ActivationConfigProperty(propertyName = "destinationType", propertyValue = "javax.jms.Queue"),
    @ActivationConfigProperty(propertyName = "destination", propertyValue = "order-events")
})
public class OrderEventsMdb implements MessageListener {

    @Override
    public void onMessage(Message message) {
        // process order event
    }
}
