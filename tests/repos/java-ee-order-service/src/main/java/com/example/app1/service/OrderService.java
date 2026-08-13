package com.example.app1.service;

import com.example.app1.producer.OrderEventProducer;
import com.example.app1.rest.FulfillmentStatusClient;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

/**
 * CDI application service for the order domain. Sits between the JAX-RS
 * resource and the producer so the call graph has a resolvable middle layer.
 */
@ApplicationScoped
public class OrderService {

    @Inject
    private OrderEventProducer orderEventProducer;

    @Inject
    private FulfillmentStatusClient fulfillmentStatusClient;

    public void placeOrder(String orderId) {
        orderEventProducer.emitOrderPlaced(orderId);
        orderEventProducer.emitFulfillmentCommand(orderId);
    }

    public int countOrders() {
        return 42;
    }

    public String fetchOrderStatus(String orderId) {
        return fulfillmentStatusClient.fetchStatus(orderId);
    }
}
