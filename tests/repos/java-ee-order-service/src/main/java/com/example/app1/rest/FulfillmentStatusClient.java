package com.example.app1.rest;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.ws.rs.client.Client;
import jakarta.ws.rs.client.ClientBuilder;
import jakarta.ws.rs.client.WebTarget;

/**
 * Synchronous HTTP client to fulfillment-service using JAX-RS
 * Client/WebTarget. Detected as an HTTP producer and matched to the
 * fulfillment status resource by normalized path template (GET verb) →
 * cross-repo HTTP link.
 *
 * <p>The path template is kept as a string literal so the deterministic
 * detector reads it; a real client would resolve the {@code {orderId}}
 * placeholder at call time.
 */
@ApplicationScoped
public class FulfillmentStatusClient {

    private final Client client = ClientBuilder.newClient();

    public String fetchStatus(String orderId) {
        WebTarget target = client.target(
            "http://fulfillment-service/api/fulfillment/status/{orderId}"
        );
        return target.request().get(String.class);
    }
}
