package com.example.app3.cdi;

import javax.enterprise.event.Observes;
import javax.inject.Singleton;

/**
 * CDI event observer in app3 (notification) — entry point via parameter
 * annotation @Observes; channel is the observed event type.
 */
@Singleton
public class InventoryObserver {

    public void onInventoryChange(@Observes InventoryChanged event) {
        // send stock alerts
    }
}