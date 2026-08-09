package com.example.notification;

import org.springframework.stereotype.Repository;

import java.util.HashMap;
import java.util.Map;

/** Resolves customer contact preferences for an order. */
@Repository
public class CustomerPreferencesRepository {

    private final Map<String, CustomerPreferences> store = new HashMap<>();

    public CustomerPreferences findByOrderId(String orderId) {
        return store.computeIfAbsent(orderId, id -> new CustomerPreferences(
            "customer+" + id + "@example.com",
            "+1000000000"
        ));
    }
}
