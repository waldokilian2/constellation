package com.example.inventory;

import org.springframework.stereotype.Service;

/** Core stock logic: reserves stock and publishes inventory updates. */
@Service
public class InventoryService {

    private final InventoryUpdateProducer updateProducer;
    private final OrderServiceClient orderServiceClient;

    public InventoryService(InventoryUpdateProducer updateProducer,
                            OrderServiceClient orderServiceClient) {
        this.updateProducer = updateProducer;
        this.orderServiceClient = orderServiceClient;
    }

    public Stock reserve(String orderId) {
        OrderSummary order = orderServiceClient.getOrder(orderId);
        Stock stock = new Stock(order.getSku(), 4);
        updateProducer.publishUpdate(stock.getSku(), stock.getStockLevel());
        return stock;
    }

    public Stock findBySku(String sku) {
        return new Stock(sku, 9);
    }
}
