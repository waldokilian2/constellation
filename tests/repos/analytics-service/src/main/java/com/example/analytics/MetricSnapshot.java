package com.example.analytics;

/**
 * Aggregate metric snapshot computed from order events.
 * Plain model consumed across the analytics entry points.
 */
public class MetricSnapshot {

    private final String metric;
    private final long value;
    private final String window;

    public MetricSnapshot(String metric, long value, String window) {
        this.metric = metric;
        this.value = value;
        this.window = window;
    }

    public String getMetric() {
        return metric;
    }

    public long getValue() {
        return value;
    }

    public String getWindow() {
        return window;
    }
}
