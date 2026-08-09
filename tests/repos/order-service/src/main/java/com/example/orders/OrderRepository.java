package com.example.orders;

import org.springframework.stereotype.Repository;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** In-memory repository of orders (stand-in for a JPA data store). */
@Repository
public class OrderRepository {

    private final Map<String, Order> store = new HashMap<>();

    public void save(Order order) {
        store.put(order.getId(), order);
    }

    public Order findById(String id) {
        return store.get(id);
    }

    public Order requireById(String id) {
        Order order = store.get(id);
        if (order == null) {
            throw new IllegalArgumentException("Order not found: " + id);
        }
        return order;
    }

    public List<Order> findByProduct(String productId) {
        List<Order> matches = new ArrayList<>();
        for (Order order : store.values()) {
            if (order.getProductId().equals(productId)) {
                matches.add(order);
            }
        }
        return matches;
    }
}
