package com.example.orders;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

/**
 * Feign client for synchronous calls to fulfillment-service.
 * The outbound HTTP call is detected as an HTTP producer (not a server-side
 * REST entry point) and matched to the fulfillment status endpoint by
 * normalized path template.
 */
@FeignClient(name = "fulfillment-service", url = "${fulfillment-service.base-url}")
public interface FulfillmentStatusClient {

    @GetMapping("/api/fulfillment/status/{orderId}")
    FulfillmentStatus getFulfillmentStatus(@PathVariable("orderId") String orderId);
}
