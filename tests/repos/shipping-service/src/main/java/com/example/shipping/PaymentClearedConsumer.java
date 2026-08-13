package com.example.shipping;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "payment-events" topic.
 *
 * <p>Cross-repo link: payment-service publishes "payment-events" (see
 * {@code PaymentEventPublisher}); this consumer establishes the
 * payment &rarr; shipping edge. Releases the shipment once the charge clears.
 */
@Component
public class PaymentClearedConsumer {

    private final ShipmentService shipmentService;

    public PaymentClearedConsumer(ShipmentService shipmentService) {
        this.shipmentService = shipmentService;
    }

    @KafkaListener(topics = "payment-events")
    public void onPaymentEvent(PaymentEvent event) {
        if ("CHARGED".equals(event.getStatus())) {
            shipmentService.ship(event.getOrderId());
        }
    }
}
