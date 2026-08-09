package com.example.notification;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

/**
 * RabbitMQ consumer — entry point on the "order-events" queue.
 * Produced by order-service; sends order-confirmation notifications.
 */
@Component
public class OrderConfirmationConsumer {

    private final NotificationService notificationService;

    public OrderConfirmationConsumer(NotificationService notificationService) {
        this.notificationService = notificationService;
    }

    @RabbitListener(queues = "order-events")
    public void handleOrderEvent(OrderEvent event) {
        if ("CREATED".equals(event.getType())) {
            notificationService.notifyOrderConfirmed(event.getOrderId());
        }
    }
}
