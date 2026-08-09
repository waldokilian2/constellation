package com.example.fulfillment;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

/**
 * RabbitMQ consumer — entry point on the "order-events" queue.
 * Receives order lifecycle events produced by order-service and creates a
 * shipment, propagating progress downstream.
 */
@Component
public class OrderEventConsumer {

    private final FulfillmentService fulfillmentService;

    public OrderEventConsumer(FulfillmentService fulfillmentService) {
        this.fulfillmentService = fulfillmentService;
    }

    @RabbitListener(queues = "order-events")
    public void handleOrderEvent(OrderEvent event) {
        if ("CREATED".equals(event.getType())) {
            fulfillmentService.createShipment(event.getOrderId());
        } else if ("PAID".equals(event.getType())) {
            fulfillmentService.releaseShipment(event.getOrderId());
        }
    }
}
