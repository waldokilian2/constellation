package com.example.jee.ws;

import javax.websocket.OnMessage;
import javax.websocket.OnOpen;
import javax.websocket.ServerEndpoint;
import javax.websocket.Session;

/**
 * Java EE WebSocket endpoint — entry points via @ServerEndpoint class path
 * + @OnMessage/@OnOpen handlers.
 */
@ServerEndpoint("/ws/orders")
public class OrderSocket {

    @OnOpen
    public void onOpen(Session session) {
        // client connected
    }

    @OnMessage
    public String onMessage(String message) {
        return "ack:" + message;
    }
}