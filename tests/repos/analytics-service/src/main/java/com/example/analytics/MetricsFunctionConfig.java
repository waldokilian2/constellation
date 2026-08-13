package com.example.analytics;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.function.Function;

/**
 * Spring Cloud Function entry point.
 *
 * <p>Detected as a {@code cloud-function} entry: a {@code @Bean} method whose
 * return type is {@code Function<…>} (method_type "FUNCTION"). The channel is
 * the bean name; the message_type is the function's input generic argument.
 */
@Configuration
public class MetricsFunctionConfig {

    @Bean
    public Function<String, MetricSnapshot> metricQuery(AnalyticsService analyticsService) {
        return metric -> analyticsService.lookup(metric);
    }
}
