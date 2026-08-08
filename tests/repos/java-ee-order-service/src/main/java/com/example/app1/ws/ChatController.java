package com.example.app1.ws;

import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.stereotype.Controller;

/** Spring STOMP/WebSocket — @MessageMapping entry point. */
@Controller
public class ChatController {

    @MessageMapping("/chat/send")
    public void onChatMessage(String payload) {
        // broadcast to subscribers
    }
}