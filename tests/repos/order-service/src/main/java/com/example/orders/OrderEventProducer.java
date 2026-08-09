package com.example.orders;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes order lifecycle events to RabbitMQ on the "order-events" channel.
 * The call graph resolves these producer calls by the field type
 * (RabbitTemplate) and links them to consumers listening on "order-events".
 */
@Service
public class OrderEventProducer {

    private final RabbitTemplate rabbitTemplate;

    public OrderEventProducer(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    public void publishCreated(Order order) {
        rabbitTemplate.convertAndSend("order-events", new OrderEvent(order.getId(), "CREATED"));
    }

    public void publishPaid(Order order) {
        rabbitTemplate.convertAndSend("order-events", new OrderEvent(order.getId(), "PAID"));
    }
}
