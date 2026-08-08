package com.example.jee.consumer;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

/**
 * Array topics — one entry point per element ("notifications" + "alerts").
 */
@Component
public class NotificationConsumer {

    @KafkaListener(topics = {"notifications", "alerts"})
    public void onNotification(String message) {
        // fan out to channels
    }
}