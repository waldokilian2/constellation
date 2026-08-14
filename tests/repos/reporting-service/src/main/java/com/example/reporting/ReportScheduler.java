package com.example.reporting;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Nightly report refresh — {@code @Scheduled} entry point.
 *
 * <p>No message channels, no outbound calls: reporting-service stays an
 * isolated island in the cross-repo graph.
 */
@Component
public class ReportScheduler {

    private final ReportGenerator reportGenerator;

    public ReportScheduler(ReportGenerator reportGenerator) {
        this.reportGenerator = reportGenerator;
    }

    @Scheduled(cron = "0 0 2 * * *")
    public void refreshReports() {
        reportGenerator.generateMonthly();
    }
}
