package com.example.notification;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Service;

@Service
public class NotificationService {

    private final EmailGateway emailGateway;
    private final SmsGateway smsGateway;

    public NotificationService(EmailGateway emailGateway, SmsGateway smsGateway) {
        this.emailGateway = emailGateway;
        this.smsGateway = smsGateway;
    }

    @KafkaListener(topics = "shipment-events")
    public void handleShipmentEvent(ShipmentEvent event) {
        String customerId = event.getCustomerId();
        if (event.getType().equals("CREATED")) {
            emailGateway.send(customerId, "Your shipment has been created");
        } else if (event.getType().equals("READY")) {
            emailGateway.send(customerId, "Your shipment is ready for delivery");
            smsGateway.send(customerId, "Shipment ready: " + event.getTrackingNumber());
        }
    }

    @RabbitListener(queues = "order-events")
    public void handleOrderEvent(OrderEvent event) {
        emailGateway.send(event.getCustomerEmail(), "Order confirmed: " + event.getOrderId());
    }
}
