package com.example.jee.ws;

import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.stereotype.Controller;

/**
 * Spring STOMP/WebSocket — @MessageMapping destination as a WEBSOCKET entry.
 */
@Controller
public class ChatController {

    @MessageMapping("/chat/send")
    public void onChatMessage(String payload) {
        // broadcast to subscribers
    }
}
