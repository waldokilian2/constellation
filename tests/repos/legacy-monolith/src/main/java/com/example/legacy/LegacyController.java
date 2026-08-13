package com.example.legacy;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Legacy health endpoint kept for operational tooling.
 *
 * <p>Deliberately isolated: the monolith talks to nobody. Its only producer
 * (see {@code LegacyJobDispatcher}) targets an orphan channel, so the repo
 * has no cross-repo links at all — an island with a gap marker.
 */
@RestController
public class LegacyController {

    @GetMapping("/legacy/api/health")
    public String health() {
        return "UP";
    }
}
