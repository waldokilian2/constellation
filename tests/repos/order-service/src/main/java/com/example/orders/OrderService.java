package com.example.orders;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.bind.annotation.*;

@Service
public class OrderService {

    private final RabbitTemplate rabbitTemplate;
    private final OrderRepository orderRepository;

    public OrderService(RabbitTemplate rabbitTemplate, OrderRepository orderRepository) {
        this.rabbitTemplate = rabbitTemplate;
        this.orderRepository = orderRepository;
    }

    @PostMapping("/api/orders")
    public Order createOrder(OrderRequest request) {
        Order order = new Order(request.getProductId(), request.getQuantity());
        order.validate();
        orderRepository.save(order);
        rabbitTemplate.convertAndSend("order-events", new OrderMessage(order));
        return order;
    }

    @GetMapping("/api/orders/{id}")
    public Order getOrder(String id) {
        return orderRepository.findById(id);
    }

    @EventListener
    public void handlePaymentConfirmed(PaymentConfirmedEvent event) {
        Order order = orderRepository.findById(event.getOrderId());
        order.markAsPaid();
        orderRepository.save(order);
        rabbitTemplate.convertAndSend("order-events", new OrderMessage(order));
    }

    @KafkaListener(topics = "inventory-updates")
    public void handleInventoryUpdate(InventoryUpdateEvent event) {
        if (event.getStockLevel() == 0) {
            orderRepository.flagOrdersAsBackordered(event.getProductId());
        }
    }
}
