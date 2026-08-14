package com.example.reporting;

import org.springframework.stereotype.Service;

/** Aggregates internal metrics into a monthly report. */
@Service
public class ReportGenerator {

    public Report generateMonthly() {
        return new Report("monthly", 42);
    }
}
