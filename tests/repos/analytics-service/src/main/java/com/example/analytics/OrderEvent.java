package com.example.analytics;

/**
 * Order event consumed from the "order-events" topic (produced by
 * order-service). Local copy so {@link OrderAnalyticsConsumer}'s handler type
 * resolves; matches the per-repo event-model convention used across the
 * Spring Boot demo family.
 */
public class OrderEvent {

    private final String orderId;
    private final String type;

    public OrderEvent(String orderId, String type) {
        this.orderId = orderId;
        this.type = type;
    }

    public String getOrderId() {
        return orderId;
    }

    public String getType() {
        return type;
    }
}
