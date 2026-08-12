package com.example.orders;

import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;

/**
 * Publishes payment-confirmation domain events.
 *
 * <p>Wires the previously-orphaned {@link PaymentEventListener}: a confirmed
 * payment publishes a {@link PaymentConfirmedEvent} via
 * {@link ApplicationEventPublisher}, so the event-listener entry point now has
 * a producer and is no longer flagged by {@code find_orphans}. Detected as an
 * {@code event-publisher} producer by the field type.
 */
@Service
public class PaymentEventPublisher {

    private final ApplicationEventPublisher eventPublisher;

    public PaymentEventPublisher(ApplicationEventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    public void paymentConfirmed(String orderId) {
        eventPublisher.publishEvent(new PaymentConfirmedEvent(orderId));
    }
}
