package com.example.notification;

import org.springframework.jms.annotation.JmsListener;
import org.springframework.stereotype.Component;

/**
 * JMS consumer on the "metrics-jobs" queue.
 *
 * <p>Cross-repo link: analytics-service produces "metrics-jobs" (see
 * {@code AnalyticsJmsProducer} via {@code JmsTemplate}); this consumer
 * establishes the analytics &rarr; notification edge. Also exercises the
 * Spring {@code @JmsListener} entry-point kind in the Spring Boot demo family.
 */
@Component
public class MetricsJobConsumer {

    private final NotificationService notificationService;

    public MetricsJobConsumer(NotificationService notificationService) {
        this.notificationService = notificationService;
    }

    @JmsListener(destination = "metrics-jobs")
    public void onMetricJob(String body) {
        notificationService.notifyOrderConfirmed(body);
    }
}
