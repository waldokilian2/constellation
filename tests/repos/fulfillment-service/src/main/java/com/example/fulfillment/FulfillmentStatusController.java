package com.example.fulfillment;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST entry point returning fulfillment status for an order.
 * Matched to order-service's Feign HTTP call by normalized path template.
 */
@RestController
public class FulfillmentStatusController {

    private final FulfillmentService fulfillmentService;

    public FulfillmentStatusController(FulfillmentService fulfillmentService) {
        this.fulfillmentService = fulfillmentService;
    }

    @GetMapping("/api/fulfillment/status/{orderId}")
    public Shipment getFulfillmentStatus(@PathVariable("orderId") String orderId) {
        return fulfillmentService.getStatus(orderId);
    }
}
