package com.example.app3.ejb;

import com.example.app3.service.NotificationService;

import javax.ejb.Schedule;
import javax.ejb.Stateless;
import javax.inject.Inject;

/**
 * EJB timer in app3 (notification) — @Schedule entry point.
 *
 * <p>Delegates to the {@link NotificationService} so the call tree resolves a
 * real (EXTRACTED) edge instead of being an empty stub.
 */
@Stateless
public class DailyReportJob {

    @Inject
    private NotificationService notificationService;

    @Schedule(hour = "2", minute = "0", persistent = false)
    public void generateReport() {
        notificationService.notifyShipmentUpdate("daily-report");
    }
}
