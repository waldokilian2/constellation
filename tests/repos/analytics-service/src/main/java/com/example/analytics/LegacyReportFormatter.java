package com.example.analytics;

/**
 * Deliberate DEAD-CODE fixture.
 *
 * <p>None of these methods are reachable from any entry point (no caller
 * invokes them), so the engine's full reachability walk flags every method as
 * <b>unreachable</b> ({@code find_dead_code}). Kept to exercise dead-code
 * detection end-to-end on the seed repos. Represents a stale helper left over
 * from a previous reporting implementation.
 */
public class LegacyReportFormatter {

    public String formatCsv(MetricSnapshot snapshot) {
        return snapshot.getMetric() + "," + snapshot.getValue() + "," + snapshot.getWindow();
    }

    public String formatJson(MetricSnapshot snapshot) {
        return "{\"metric\":\"" + snapshot.getMetric()
            + "\",\"value\":" + snapshot.getValue() + "}";
    }

    public String banner(String title) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < title.length() + 4; i++) {
            sb.append("=");
        }
        sb.append("\n  ").append(title).append("  \n");
        for (int i = 0; i < title.length() + 4; i++) {
            sb.append("=");
        }
        return sb.toString();
    }
}
