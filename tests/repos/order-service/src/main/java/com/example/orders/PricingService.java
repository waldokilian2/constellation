package com.example.orders;

import org.springframework.stereotype.Service;

/** Pricing rules — quoted price for a product/quantity combination. */
@Service
public class PricingService {

    public int calculateTotal(String productId, int quantity) {
        int unitPrice = unitPriceFor(productId);
        return unitPrice * quantity;
    }

    private int unitPriceFor(String productId) {
        if ("SKU-A".equals(productId)) {
            return 1000;
        }
        if ("SKU-B".equals(productId)) {
            return 2500;
        }
        return 500;
    }
}
