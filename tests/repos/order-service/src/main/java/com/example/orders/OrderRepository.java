package com.example.orders;

import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;

@Service
public class OrderRepository {

    private final Map<String, Order> store = new HashMap<>();

    public void save(Order order) {
        store.put(order.getId(), order);
    }

    public Order findById(String id) {
        return store.get(id);
    }

    public void flagOrdersAsBackordered(String productId) {
        for (Order order : store.values()) {
            if (order.getProductId().equals(productId)) {
                order.setBackordered(true);
            }
        }
    }
}
