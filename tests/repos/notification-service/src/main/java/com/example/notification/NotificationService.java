package com.example.notification;

import org.springframework.stereotype.Service;

/**
 * Orchestrates outbound notifications. Consumed by the shipment and order
 * event listeners.
 *
 * <p>Confidence-tag demo:
 * <ul>
 *   <li>{@code notifier.send(...)} resolves via the {@link Notifier} interface
 *       to two implementations → {@code AMBIGUOUS}.</li>
 *   <li>{@code templateEngine.render(...)} targets a type that is absent from
 *       this repo → {@code INFERRED}.</li>
 *   <li>{@code preferencesRepository.find(...)} resolves to a concrete method
 *       → {@code EXTRACTED}.</li>
 * </ul>
 */
@Service
public class NotificationService {

    private final Notifier notifier;
    private final CustomerPreferencesRepository preferencesRepository;
    private final TemplateEngine templateEngine;

    public NotificationService(Notifier notifier,
                               CustomerPreferencesRepository preferencesRepository,
                               TemplateEngine templateEngine) {
        this.notifier = notifier;
        this.preferencesRepository = preferencesRepository;
        this.templateEngine = templateEngine;
    }

    public void notifyShipmentCreated(String orderId) {
        CustomerPreferences prefs = preferencesRepository.findByOrderId(orderId);
        String message = templateEngine.render("shipment.created", orderId);
        notifier.send(prefs.getEmail(), message);
    }

    public void notifyShipmentReady(String orderId) {
        CustomerPreferences prefs = preferencesRepository.findByOrderId(orderId);
        String message = templateEngine.render("shipment.ready", orderId);
        notifier.send(prefs.getPhone(), message);
    }

    public void notifyOrderConfirmed(String orderId) {
        CustomerPreferences prefs = preferencesRepository.findByOrderId(orderId);
        String message = templateEngine.render("order.confirmed", orderId);
        notifier.send(prefs.getEmail(), message);
    }
}
