package com.example.users;

/** A registered user. */
public class User {

    private final String id;
    private final String role;

    public User(String id, String role) {
        this.id = id;
        this.role = role;
    }

    public String getId() { return id; }
    public String getRole() { return role; }
}
