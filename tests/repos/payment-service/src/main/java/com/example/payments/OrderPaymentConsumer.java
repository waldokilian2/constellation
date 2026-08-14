package com.example.payments;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Kafka consumer on the "order-events" topic.
 *
 * <p>Cross-repo link: order-service publishes "order-events" (see
 * {@code OrderEventProducer}); this consumer establishes the
 * order &rarr; payment edge. Charges the order once payment details arrive.
 */
@Component
public class OrderPaymentConsumer {

    private final PaymentService paymentService;

    public OrderPaymentConsumer(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    @KafkaListener(topics = "order-events")
    public void onOrderEvent(OrderEvent event) {
        if ("CREATED".equals(event.getStatus())) {
            paymentService.charge(event.getOrderId());
        }
    }
}
