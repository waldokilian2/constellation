package com.example.reporting;

/** A generated report. */
public class Report {

    private final String period;
    private final int orders;

    public Report(String period, int orders) {
        this.period = period;
        this.orders = orders;
    }

    public String getPeriod() { return period; }
    public int getOrders() { return orders; }
}
