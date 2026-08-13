package com.example.analytics;

import io.awspring.cloud.sqs.annotation.SqsListener;
import org.springframework.stereotype.Component;

/**
 * AWS SQS consumer entry point.
 *
 * <p>Detected as an {@code sqs-consumer}: {@code @SqsListener} with a bare
 * queue-name argument. Deliberate GAP fixture: no producer in any repo emits
 * to "analytics.queue" (SQS sends are not statically detectable), so this is
 * an <b>orphan consumer</b> surfaced by {@code find_orphans}.
 */
@Component
public class SqsEventConsumer {

    private final AnalyticsService analyticsService;

    public SqsEventConsumer(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @SqsListener("analytics.queue")
    public void onMetric(String body) {
        analyticsService.record("sqs", body, 1L);
    }
}
