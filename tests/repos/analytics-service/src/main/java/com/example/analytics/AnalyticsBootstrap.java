package com.example.analytics;

import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;

/**
 * Lifecycle entry points.
 *
 * <p>Two deterministic signals:
 * <ul>
 *   <li>{@code @PostConstruct init()} &rarr; a {@code lifecycle} entry,
 *       channel {@code @PostConstruct:init}.</li>
 *   <li>{@code implements CommandLineRunner} &rarr; the {@code run(...)} method
 *       is a {@code lifecycle} entry, channel {@code @CommandLineRunner:run}.</li>
 * </ul>
 */
@Component
public class AnalyticsBootstrap implements CommandLineRunner {

    private final AnalyticsService analyticsService;

    public AnalyticsBootstrap(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @PostConstruct
    public void init() {
        analyticsService.lookup("startup.gauge");
    }

    @Override
    public void run(String... args) {
        analyticsService.aggregate("bootstrap", "warmup", 1L);
    }
}
