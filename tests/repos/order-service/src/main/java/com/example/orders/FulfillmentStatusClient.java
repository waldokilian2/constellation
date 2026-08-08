package com.example.orders;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;

/**
 * Fixture: order-service calls fulfillment-service synchronously via Feign.
 * The outbound call must NOT be detected as a server-side REST entry point.
 */
@FeignClient(name = "fulfillment-service", url = "${fulfillment-service.base-url}")
public interface FulfillmentStatusClient {

    @GetMapping("/api/fulfillment/status/{orderId}")
    FulfillmentStatus getFulfillmentStatus(String orderId);
}