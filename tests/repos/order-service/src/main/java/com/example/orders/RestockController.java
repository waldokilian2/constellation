package com.example.orders;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST entry point that emits inventory updates.
 *
 * <p>Wires the previously-orphaned "inventory-updates" consumer: a restock
 * call publishes an {@link InventoryUpdateEvent} via
 * {@link InventoryUpdateProducer}, so the channel now has a producer and is no
 * longer flagged by {@code find_orphans}.
 */
@RestController
public class RestockController {

    private final InventoryUpdateProducer inventoryUpdateProducer;

    public RestockController(InventoryUpdateProducer inventoryUpdateProducer) {
        this.inventoryUpdateProducer = inventoryUpdateProducer;
    }

    @PostMapping("/api/restock")
    public String restock(@RequestBody RestockRequest request) {
        inventoryUpdateProducer.publishRestock(request.getProductId(), request.getUnits());
        return "{\"restocked\":true}";
    }
}
