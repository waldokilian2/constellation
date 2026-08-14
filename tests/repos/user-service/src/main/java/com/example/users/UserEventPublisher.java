package com.example.users;

import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes user activity events on the "user-events" channel.
 *
 * <p>Cross-repo link: recommendation-service consumes "user-events" (see
 * {@code UserEventConsumer}), establishing the user &rarr; recommendation edge.
 */
@Service
public class UserEventPublisher {

    private final RabbitTemplate rabbitTemplate;

    public UserEventPublisher(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    public void publishViewed(User user, String productId) {
        rabbitTemplate.convertAndSend("user-events",
                new UserEvent(user.getId(), "VIEWED", productId));
    }
}
