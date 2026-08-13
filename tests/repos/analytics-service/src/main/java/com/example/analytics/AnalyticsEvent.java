package com.example.analytics;

/**
 * Event payload published on the "analytics-events" Kafka topic. Consumed by
 * order-service, closing the order &lt;-&gt; analytics dependency cycle.
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
