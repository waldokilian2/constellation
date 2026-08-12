package com.example.analytics;

import org.springframework.stereotype.Repository;

/**
 * In-memory analytics store. Methods resolve to concrete definitions so the
 * call graph yields {@code EXTRACTED} edges from the entry points that use it.
 */
@Repository
public class AnalyticsRepository {

    public MetricSnapshot find(String metric) {
        return new MetricSnapshot(metric, 0L, "none");
    }

    public MetricSnapshot save(MetricSnapshot snapshot) {
        return snapshot;
    }

    public MetricSnapshot increment(String metric, long delta) {
        return new MetricSnapshot(metric, delta, "live");
    }
}
