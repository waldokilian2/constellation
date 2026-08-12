package com.example.analytics;

import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;

/**
 * Publishes in-process domain events via {@link ApplicationEventPublisher}.
 *
 * <p>Detected as an {@code event-publisher} producer: the field type is
 * {@code ApplicationEventPublisher} and the method is {@code publishEvent}.
 * The channel/message type is read from the {@code new MetricComputedEvent(...)}
 * object creation argument.
 */
@Service
public class AnalyticsEventPublisher {

    private final ApplicationEventPublisher eventPublisher;

    public AnalyticsEventPublisher(ApplicationEventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    public void publishMetricComputed(MetricSnapshot snapshot) {
        eventPublisher.publishEvent(new MetricComputedEvent(snapshot));
    }
}
