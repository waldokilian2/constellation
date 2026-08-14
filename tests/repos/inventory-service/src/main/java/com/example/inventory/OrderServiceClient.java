package com.example.inventory;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

/**
 * Feign client for synchronous calls to order-service.
 *
 * <p>The outbound call is detected as an {@code http-call} producer and
 * matched to order-service's {@code /api/orders/{id}} endpoint by normalized
 * path template, establishing the inventory &rarr; order HTTP edge.
 */
@FeignClient(name = "order-service", url = "${order-service.base-url}")
public interface OrderServiceClient {

    @GetMapping("/api/orders/{id}")
    OrderSummary getOrder(@PathVariable("id") String id);
}
