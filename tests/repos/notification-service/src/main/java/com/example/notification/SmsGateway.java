package com.example.notification;

import org.springframework.stereotype.Service;

@Service
public class SmsGateway {
    public void send(String phoneNumber, String message) {
        System.out.println("SMS to " + phoneNumber + ": " + message);
    }
}
