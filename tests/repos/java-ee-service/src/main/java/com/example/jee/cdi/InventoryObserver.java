package com.example.jee.cdi;

import javax.enterprise.event.Observes;
import javax.inject.Singleton;

/**
 * CDI event observer — entry point with a parameter annotated @Observes;
 * the channel is the observed event type.
 */
@Singleton
public class InventoryObserver {

    public void onInventoryChange(@Observes InventoryChanged event) {
        // react to inventory change
    }
}