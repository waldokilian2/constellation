package com.example.users;

import org.springframework.stereotype.Service;

/** Core user logic: profiles, shipment notifications, recommendations. */
@Service
public class UserService {

    private final UserEventPublisher eventPublisher;

    public UserService(UserEventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    public User findById(String id) {
        return new User(id, "customer");
    }

    public void notifyShipment(String orderId) {
        // delivery notification — no reply channel
    }

    public void storeRecommendation(String userId, String productId) {
        User user = findById(userId);
        eventPublisher.publishViewed(user, productId);
    }

    public String getShipmentStatus(String id) {
        return "IN_TRANSIT";
    }
}
