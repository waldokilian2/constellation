package com.example.app3.ejb;

import javax.ejb.Schedule;
import javax.ejb.Stateless;

/** EJB timer in app3 (notification) — @Schedule entry point. */
@Stateless
public class DailyReportJob {

    @Schedule(hour = "2", minute = "0", persistent = false)
    public void generateReport() {
        // send daily digest
    }
}