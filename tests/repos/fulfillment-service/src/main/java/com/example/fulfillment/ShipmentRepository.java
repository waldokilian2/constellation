package com.example.fulfillment;

import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;

@Service
public class ShipmentRepository {

    private final Map<String, Shipment> store = new HashMap<>();

    public void save(Shipment shipment) {
        store.put(shipment.getId(), shipment);
    }

    public Shipment findByOrderId(String orderId) {
        return store.values().stream()
            .filter(s -> s.getOrderId().equals(orderId))
            .findFirst()
            .orElse(null);
    }
}
