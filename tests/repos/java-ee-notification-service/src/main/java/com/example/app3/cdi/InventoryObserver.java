package com.example.app3.cdi;

import com.example.app3.service.NotificationService;

import javax.enterprise.event.Observes;
import javax.inject.Inject;
import javax.inject.Singleton;

/**
 * CDI event observer in app3 (notification) — entry point via parameter
 * annotation @Observes; channel is the observed event type.
 *
 * <p>Delegates to the {@link NotificationService} so the call tree resolves a
 * real (EXTRACTED) edge instead of being an empty stub.
 *
 * <p>Deliberate GAP fixture: {@code InventoryChanged} is observed but no
 * in-repo code publishes it (CDI events are injected from elsewhere /
 * external systems), so it surfaces as an <b>orphan consumer</b> in
 * {@code find_orphans} — mirroring the SQS orphan in the Spring family.
 */
@Singleton
public class InventoryObserver {

    @Inject
    private NotificationService notificationService;

    public void onInventoryChange(@Observes InventoryChanged event) {
        notificationService.notifyShipmentUpdate(event.getSku());
    }
}
