package com.example.notification;

import org.springframework.stereotype.Service;

/** Email delivery channel implementing {@link Notifier}. */
@Service
public class EmailNotifier implements Notifier {

    public void send(String recipient, String message) {
        System.out.println("Email to " + recipient + ": " + message);
    }
}
