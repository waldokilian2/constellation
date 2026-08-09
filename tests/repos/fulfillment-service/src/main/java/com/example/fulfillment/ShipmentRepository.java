package com.example.fulfillment;

import org.springframework.stereotype.Repository;

import java.util.HashMap;
import java.util.Map;

/** In-memory repository of shipments. */
@Repository
public class ShipmentRepository {

    private final Map<String, Shipment> store = new HashMap<>();

    public void save(Shipment shipment) {
        store.put(shipment.getId(), shipment);
    }

    public Shipment findById(String id) {
        return store.get(id);
    }

    public Shipment findByOrderId(String orderId) {
        for (Shipment shipment : store.values()) {
            if (shipment.getOrderId().equals(orderId)) {
                return shipment;
            }
        }
        return null;
    }
}
