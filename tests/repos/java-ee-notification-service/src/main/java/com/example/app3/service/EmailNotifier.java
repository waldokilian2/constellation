package com.example.app3.service;

import jakarta.enterprise.context.ApplicationScoped;

/** Email delivery channel implementing {@link Notifier}. */
@ApplicationScoped
public class EmailNotifier implements Notifier {

    @Override
    public void send(String recipient, String message) {
        System.out.println("Email to " + recipient + ": " + message);
    }
}
