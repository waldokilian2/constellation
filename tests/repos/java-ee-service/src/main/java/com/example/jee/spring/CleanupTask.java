package com.example.jee.spring;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Spring @Scheduled — scheduled task entry point (cron form).
 */
@Component
public class CleanupTask {

    @Scheduled(cron = "0 3 * * * *")
    public void purgeOldRecords() {
        // housekeeping
    }
}