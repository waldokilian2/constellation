package com.example.notification;

import org.springframework.stereotype.Service;

@Service
public class EmailGateway {
    public void send(String recipient, String message) {
        // In real code, would connect to SMTP
        System.out.println("Email to " + recipient + ": " + message);
    }
}
