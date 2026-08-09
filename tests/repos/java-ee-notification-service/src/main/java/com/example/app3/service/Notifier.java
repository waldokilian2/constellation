package com.example.app3.service;

/** Delivery-channel strategy — two impls (Email/Sms) → AMBIGUOUS resolution. */
public interface Notifier {

    void send(String recipient, String message);
}
