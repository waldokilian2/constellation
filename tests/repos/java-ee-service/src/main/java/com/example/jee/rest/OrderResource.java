package com.example.jee.rest;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;

/**
 * JAX-RS resource — exercises @Path (class + method) with HTTP-verb
 * annotations. Class-level prefix must join the method-level path.
 */
@Path("/api/orders")
public class OrderResource {

    @GET
    @Path("/summary")
    public String summary() {
        return "{}";
    }

    @GET
    @Path("/{id}")
    public String byId(@PathParam("id") String id) {
        return "{\"id\":\"" + id + "\"}";
    }

    @POST
    @Path("/")
    public String create(String body) {
        return "{\"created\":true}";
    }
}
