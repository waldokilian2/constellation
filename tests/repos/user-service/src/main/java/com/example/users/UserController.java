package com.example.users;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST entry points for user profiles.
 */
@RestController
@RequestMapping("/api/users")
public class UserController {

    private final UserService userService;

    public UserController(UserService userService) {
        this.userService = userService;
    }

    @GetMapping("/{id}")
    public User getUser(@PathVariable("id") String id) {
        return userService.findById(id);
    }

    @GetMapping("/{id}/shipments")
    public String getShipmentStatus(@PathVariable("id") String id) {
        return userService.getShipmentStatus(id);
    }
}
