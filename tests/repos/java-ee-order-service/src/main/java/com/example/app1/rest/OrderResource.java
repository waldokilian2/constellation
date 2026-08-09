package com.example.app1.rest;

import com.example.app1.service.OrderService;

import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;

/**
 * JAX-RS resource — REST entry points with joined class+method paths.
 * Delegates to the {@link OrderService} application service so the call graph
 * resolves a deep, EXTRACTED tree (resource → service → producer).
 */
@Path("/api/orders")
public class OrderResource {

    @Inject
    private OrderService orderService;

    @GET
    @Path("/summary")
    public String summary() {
        return "{\"count\":" + orderService.countOrders() + "}";
    }

    @POST
    @Path("/")
    public String create(String body) {
        orderService.placeOrder("ORD-1");
        return "{\"created\":true}";
    }

    @GET
    @Path("/{orderId}/status")
    public String status(@PathParam("orderId") String orderId) {
        return orderService.fetchOrderStatus(orderId);
    }
}
