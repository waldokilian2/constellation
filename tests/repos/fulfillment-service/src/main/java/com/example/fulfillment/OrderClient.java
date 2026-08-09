package com.example.fulfillment;

import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

/**
 * Synchronous HTTP client back to order-service (reverse HTTP edge).
 *
 * <p>order-service calls fulfillment via Feign; fulfillment calls back here
 * via RestTemplate. Both are detected as HTTP producers and matched to the
 * corresponding REST entry points in the other repo by normalized path.
 */
@Service
public class OrderClient {

    private final RestTemplate restTemplate;

    public OrderClient(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public void notifyOrderFulfilled(String orderId) {
        restTemplate.put("http://order-service/api/orders/" + orderId + "/fulfilled");
    }
}
