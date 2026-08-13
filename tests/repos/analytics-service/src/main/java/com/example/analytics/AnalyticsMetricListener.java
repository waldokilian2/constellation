package com.example.analytics;

import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/**
 * In-process event listener — entry point via {@code @EventListener}.
 *
 * <p>Consumes the {@link MetricComputedEvent} published by
 * {@link AnalyticsEventPublisher} (same repo), so the event-publisher edge is
 * wired and not an orphan.
 */
@Component
public class AnalyticsMetricListener {

    private final AnalyticsService analyticsService;

    public AnalyticsMetricListener(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @EventListener
    public void onMetricComputed(MetricComputedEvent event) {
        MetricSnapshot snapshot = event.getSnapshot();
        analyticsService.aggregate("event", snapshot.getMetric(), snapshot.getValue());
    }
}
