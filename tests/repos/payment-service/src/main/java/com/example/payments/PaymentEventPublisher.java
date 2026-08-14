package com.example.payments;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes payment lifecycle events on the "payment-events" channel.
 *
 * <p>Cross-repo link: shipping-service consumes "payment-events" (see
 * {@code PaymentClearedConsumer}), establishing the payment &rarr; shipping
 * edge once the charge settles.
 */
@Service
public class PaymentEventPublisher {

    private final RabbitTemplate rabbitTemplate;

    public PaymentEventPublisher(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    public void publishCharged(Payment payment) {
        rabbitTemplate.convertAndSend("payment-events",
                new PaymentEvent(payment.getOrderId(), "CHARGED"));
    }

    public void publishFailed(Payment payment) {
        rabbitTemplate.convertAndSend("payment-events",
                new PaymentEvent(payment.getOrderId(), "FAILED"));
    }
}
