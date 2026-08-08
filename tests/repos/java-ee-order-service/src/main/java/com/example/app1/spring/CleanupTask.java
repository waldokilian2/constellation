package com.example.app1.spring;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Spring @Scheduled cron — scheduled task entry point. */
@Component
public class CleanupTask {

    @Scheduled(cron = "0 3 * * * *")
    public void purgeOldRecords() {
        // housekeeping
    }
}