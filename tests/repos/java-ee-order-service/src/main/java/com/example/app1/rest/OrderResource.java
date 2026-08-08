package com.example.app1.rest;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;

/** JAX-RS resource — REST entry points with joined class+method paths. */
@Path("/api/orders")
public class OrderResource {

    @GET
    @Path("/summary")
    public String summary() {
        return "{}";
    }

    @POST
    @Path("/")
    public String create(String body) {
        return "{\"created\":true}";
    }
}