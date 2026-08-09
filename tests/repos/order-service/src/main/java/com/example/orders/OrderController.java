package com.example.orders;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * REST entry points for order management. Each annotated method is detected
 * as a server-side REST endpoint and its call tree built from here.
 */
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @PostMapping
    public Order createOrder(@RequestBody OrderRequest request) {
        return orderService.createOrder(request);
    }

    @GetMapping("/{id}")
    public Order getOrder(@PathVariable("id") String id) {
        return orderService.getOrder(id);
    }

    @GetMapping("/{id}/status")
    public OrderStatus getOrderStatus(@PathVariable("id") String id) {
        return orderService.getOrderStatus(id);
    }

    @PutMapping("/{orderId}/fulfilled")
    public Order markFulfilled(@PathVariable("orderId") String orderId) {
        return orderService.markFulfilled(orderId);
    }
}
