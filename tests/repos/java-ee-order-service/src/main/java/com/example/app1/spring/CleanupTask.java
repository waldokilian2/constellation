package com.example.app1.spring;

import com.example.app1.service.OrderService;

import jakarta.inject.Inject;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Spring @Scheduled cron — scheduled task entry point.
 *
 * <p>Delegates to the {@link OrderService} so the call tree resolves a real
 * (EXTRACTED) edge instead of being an empty stub.
 */
@Component
public class CleanupTask {

    @Inject
    private OrderService orderService;

    @Scheduled(cron = "0 3 * * * *")
    public void purgeOldRecords() {
        int openOrders = orderService.countOrders();
        if (openOrders > 1000) {
            orderService.countOrders();
        }
    }
}
