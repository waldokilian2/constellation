package com.example.app1.ws;

import com.example.app1.service.OrderService;

import jakarta.inject.Inject;
import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.stereotype.Controller;

/**
 * Spring STOMP/WebSocket — @MessageMapping entry point.
 *
 * <p>Delegates to the {@link OrderService} so the call tree resolves a real
 * (EXTRACTED) edge instead of being an empty stub.
 */
@Controller
public class ChatController {

    @Inject
    private OrderService orderService;

    @MessageMapping("/chat/send")
    public void onChatMessage(String payload) {
        orderService.placeOrder(payload);
    }
}
