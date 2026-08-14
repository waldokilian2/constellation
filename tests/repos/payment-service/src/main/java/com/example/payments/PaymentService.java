package com.example.payments;

import org.springframework.stereotype.Service;

/** Core payment logic: charges an order and records the result. */
@Service
public class PaymentService {

    private final PaymentEventPublisher publisher;
    private final FulfillmentStatusClient fulfillmentStatusClient;

    public PaymentService(PaymentEventPublisher publisher,
                          FulfillmentStatusClient fulfillmentStatusClient) {
        this.publisher = publisher;
        this.fulfillmentStatusClient = fulfillmentStatusClient;
    }

    public Payment charge(String orderId) {
        Payment payment = new Payment(orderId, "CHARGED");
        publisher.publishCharged(payment);
        fulfillmentStatusClient.getFulfillmentStatus(orderId);
        return payment;
    }

    public Payment findByOrder(String orderId) {
        return new Payment(orderId, "SETTLED");
    }
}
