package com.example.analytics;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Deliberate THIN-HANDLER fixture.
 *
 * <p>{@code ping()} is a REST entry point whose body performs no non-trivial
 * call, so the engine marks it {@code thin: true} ({@code find_dead_code}
 * surfaces it as a no-op handler). The other endpoint resolves a real call so
 * the controller is not entirely a stub.
 */
@RestController
public class HealthCheckController {

    @GetMapping("/ping")
    public String ping() {
        return "pong";
    }

    @GetMapping("/version")
    public String version() {
        return versionString();
    }

    private String versionString() {
        return "analytics-1.0.0";
    }
}
