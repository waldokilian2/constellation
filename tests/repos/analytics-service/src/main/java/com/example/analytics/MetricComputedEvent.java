package com.example.analytics;

/**
 * In-process event payload published by {@link AnalyticsEventPublisher} and
 * observed by {@link AnalyticsMetricListener}. Carries the channel name
 * ("MetricComputedEvent") that links the publisher to the listener.
 */
public class MetricComputedEvent {

    private final MetricSnapshot snapshot;

    public MetricComputedEvent(MetricSnapshot snapshot) {
        this.snapshot = snapshot;
    }

    public MetricSnapshot getSnapshot() {
        return snapshot;
    }
}
