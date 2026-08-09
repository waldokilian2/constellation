package com.example.notification;

/**
 * Strategy interface for delivering notifications. Deliberately has two
 * implementations (EmailNotifier, SmsNotifier), so a call through this
 * interface resolves to multiple candidates → {@code AMBIGUOUS} confidence.
 */
public interface Notifier {

    void send(String recipient, String message);
}
