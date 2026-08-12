package com.example.orders;

/**
 * Analytics event payload consumed from the "analytics-events" topic (produced
 * by analytics-service). Minimal model local to order-service so the
 * {@code @KafkaListener} handler type resolves.
 */
public class AnalyticsEvent {

    private final String orderId;
    private final String metric;
    private final long value;

    public AnalyticsEvent(String orderId, String metric, long value) {
        this.orderId = orderId;
        this.metric = metric;
        this.value = value;
    }

    public String getOrderId() {
        return orderId;
    }

    public String getMetric() {
        return metric;
    }

    public long getValue() {
        return value;
    }
}
