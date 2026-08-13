package com.example.reporting;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST entry points for reporting.
 *
 * <p>Deliberately isolated: this service shares no channels and makes no
 * outbound calls, so it floats as a lone star in the galaxy view.
 */
@RestController
@RequestMapping("/api/reports")
public class ReportController {

    private final ReportGenerator reportGenerator;

    public ReportController(ReportGenerator reportGenerator) {
        this.reportGenerator = reportGenerator;
    }

    @GetMapping("/monthly")
    public Report monthlyReport() {
        return reportGenerator.generateMonthly();
    }
}
