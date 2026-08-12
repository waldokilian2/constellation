package com.example.analytics;

/**
 * Standalone CLI entry point ({@code public static void main}).
 *
 * <p>Detected as a {@code main} entry point because it is a static
 * {@code main(String[])} whose body is NOT a Spring Boot launcher
 * (no {@code SpringApplication.run}), so it is treated as a genuine JVM entry.
 */
public class AnalyticsDataCli {

    private final AnalyticsService analyticsService;

    public AnalyticsDataCli(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    public static void main(String[] args) {
        if (args.length < 1) {
            System.err.println("usage: AnalyticsDataCli <metric>");
            return;
        }
        System.out.println("querying metric: " + args[0]);
    }
}
