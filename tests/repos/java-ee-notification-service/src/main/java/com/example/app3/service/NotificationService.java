package com.example.app3.service;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

/**
 * CDI application service for notifications. Consumed by the shipment
 * tracking consumer; routes through the {@link Notifier} strategy.
 */
@ApplicationScoped
public class NotificationService {

    @Inject
    private Notifier notifier;

    public void notifyShipmentUpdate(String orderId) {
        notifier.send("customer+" + orderId + "@example.com", "Shipment " + orderId + " update");
    }
}
