package com.example.app3.ws;

import javax.websocket.OnMessage;
import javax.websocket.OnOpen;
import javax.websocket.ServerEndpoint;
import javax.websocket.Session;

/** Java EE WebSocket endpoint in app3 — @ServerEndpoint + handler entries. */
@ServerEndpoint("/ws/notifications")
public class NotificationSocket {

    @OnOpen
    public void onOpen(Session session) {
        // subscriber connected
    }

    @OnMessage
    public String onMessage(String message) {
        return "subscribed:" + message;
    }
}