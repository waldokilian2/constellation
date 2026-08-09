package com.example.app2.rest;

import com.example.app2.service.FulfillmentService;

import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;

/**
 * JAX-RS resource returning fulfillment status for an order. Matched to
 * app1's JAX-RS Client HTTP call by normalized path template (GET verb) →
 * cross-repo HTTP link.
 */
@Path("/api/fulfillment/status")
public class FulfillmentStatusResource {

    @Inject
    private FulfillmentService fulfillmentService;

    @GET
    @Path("/{orderId}")
    public String status(@PathParam("orderId") String orderId) {
        return fulfillmentService.statusFor(orderId);
    }
}
