package com.example.analytics;

import org.springframework.graphql.data.method.annotation.MutationMapping;
import org.springframework.graphql.data.method.annotation.QueryMapping;
import org.springframework.stereotype.Controller;

/**
 * Spring for GraphQL entry points.
 *
 * <p>Detected as {@code graphql} entries: {@code @QueryMapping} (method_type
 * "Query") and {@code @MutationMapping} (method_type "Mutation"). The channel
 * is the operation name (annotation {@code name}/value, falling back to the
 * method name).
 */
@Controller
public class AnalyticsGraphQLController {

    private final AnalyticsService analyticsService;

    public AnalyticsGraphQLController(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @QueryMapping
    public MetricSnapshot metric(String metric) {
        return analyticsService.lookup(metric);
    }

    @MutationMapping(name = "recordMetric")
    public MetricSnapshot recordMetric(String orderId, String metric, long value) {
        return analyticsService.record(orderId, metric, value);
    }
}
