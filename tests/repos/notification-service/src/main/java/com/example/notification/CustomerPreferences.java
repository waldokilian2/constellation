package com.example.notification;

/** Contact preferences for a customer (email + phone). */
public class CustomerPreferences {

    private final String email;
    private final String phone;

    public CustomerPreferences(String email, String phone) {
        this.email = email;
        this.phone = phone;
    }

    public String getEmail() { return email; }
    public String getPhone() { return phone; }
}
