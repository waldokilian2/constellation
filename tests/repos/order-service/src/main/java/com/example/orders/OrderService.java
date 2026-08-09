package com.example.orders;

import org.springframework.stereotype.Service;

/**
 * Application service orchestrating the order lifecycle.
 *
 * <p>This class drives a deep, resolvable call tree: each method fans out to
 * a repository, a pricing service, an event producer, and (for status) the
 * Feign client — all of which resolve to concrete definitions in this repo,
 * yielding {@code EXTRACTED} confidence edges.
 */
@Service
public class OrderService {

    private final OrderRepository orderRepository;
    private final OrderEventProducer eventProducer;
    private final PricingService pricingService;
    private final FulfillmentStatusClient fulfillmentClient;

    public OrderService(OrderRepository orderRepository,
                        OrderEventProducer eventProducer,
                        PricingService pricingService,
                        FulfillmentStatusClient fulfillmentClient) {
        this.orderRepository = orderRepository;
        this.eventProducer = eventProducer;
        this.pricingService = pricingService;
        this.fulfillmentClient = fulfillmentClient;
    }

    public Order createOrder(OrderRequest request) {
        Order order = new Order(
            request.getProductId(),
            request.getQuantity(),
            request.getCustomerEmail()
        );
        order.validate();
        order.setTotalCents(
            pricingService.calculateTotal(request.getProductId(), request.getQuantity())
        );
        orderRepository.save(order);
        eventProducer.publishCreated(order);
        return order;
    }

    public Order getOrder(String id) {
        return orderRepository.requireById(id);
    }

    public OrderStatus getOrderStatus(String id) {
        Order order = orderRepository.requireById(id);
        FulfillmentStatus fulfillment = fulfillmentClient.getFulfillmentStatus(id);
        return new OrderStatus(order.getStatus(), fulfillment.getStatus());
    }

    public Order confirmPaid(String orderId) {
        Order order = orderRepository.requireById(orderId);
        order.markPaid();
        orderRepository.save(order);
        eventProducer.publishPaid(order);
        return order;
    }

    public Order markFulfilled(String orderId) {
        Order order = orderRepository.requireById(orderId);
        order.markFulfilled();
        orderRepository.save(order);
        return order;
    }
}
