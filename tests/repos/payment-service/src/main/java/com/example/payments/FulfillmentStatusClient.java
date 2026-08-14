package com.example.payments;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

/**
 * Synchronous HTTP client to fulfillment-service.
 *
 * <p>The outbound {@code RestTemplate.getForObject} call is detected as an
 * {@code http-call} producer and matched to the fulfillment status endpoint
 * by normalized path template, establishing the payment &rarr; fulfillment
 * HTTP edge.
 */
@Component
public class FulfillmentStatusClient {

    private final RestTemplate restTemplate;

    public FulfillmentStatusClient(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public FulfillmentStatus getFulfillmentStatus(String orderId) {
        return restTemplate.getForObject(
                "http://fulfillment-service/api/fulfillment/status/{orderId}",
                FulfillmentStatus.class, orderId);
    }
}
