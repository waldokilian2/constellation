package com.example.app3.ws;

import com.example.app3.service.NotificationService;

import javax.inject.Inject;
import javax.websocket.OnMessage;
import javax.websocket.OnOpen;
import javax.websocket.ServerEndpoint;
import javax.websocket.Session;

/**
 * Java EE WebSocket endpoint in app3 — @ServerEndpoint + handler entries.
 *
 * <p>Both handlers delegate to the {@link NotificationService} so the entry
 * points resolve real (EXTRACTED) call trees instead of being empty stubs.
 */
@ServerEndpoint("/ws/notifications")
public class NotificationSocket {

    @Inject
    private NotificationService notificationService;

    @OnOpen
    public void onOpen(Session session) {
        notificationService.notifyShipmentUpdate("subscriber:" + session.getId());
    }

    @OnMessage
    public String onMessage(String message) {
        notificationService.notifyShipmentUpdate(message);
        return "subscribed:" + message;
    }
}
