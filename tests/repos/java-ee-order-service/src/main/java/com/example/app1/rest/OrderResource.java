package com.example.app1.rest;

import com.example.app1.producer.OrderEventProducer;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;

/** JAX-RS resource — REST entry points with joined class+method paths. */
@Path("/api/orders")
public class OrderResource {

    private final OrderEventProducer orderEventProducer;

    public OrderResource(OrderEventProducer orderEventProducer) {
        this.orderEventProducer = orderEventProducer;
    }

    @GET
    @Path("/summary")
    public String summary() {
        return "{}";
    }

    @POST
    @Path("/")
    public String create(String body) {
        orderEventProducer.emitOrderPlaced("ORD-1");
        return "{\"created\":true}";
    }
}