package com.example.app3.service;

import jakarta.enterprise.context.ApplicationScoped;

/** SMS delivery channel implementing {@link Notifier}. */
@ApplicationScoped
public class SmsNotifier implements Notifier {

    @Override
    public void send(String recipient, String message) {
        System.out.println("SMS to " + recipient + ": " + message);
    }
}
