package com.example.notification;

import org.springframework.stereotype.Service;

/** SMS delivery channel implementing {@link Notifier}. */
@Service
public class SmsNotifier implements Notifier {

    public void send(String recipient, String message) {
        System.out.println("SMS to " + recipient + ": " + message);
    }
}
