package com.example.jee.ejb;

import javax.ejb.Schedule;
import javax.ejb.Stateless;

/**
 * EJB timer — entry point via @Schedule (minute/hour/day attributes).
 */
@Stateless
public class DailyReportJob {

    @Schedule(hour = "2", minute = "0", persistent = false)
    public void generateReport() {
        // run nightly report
    }
}
