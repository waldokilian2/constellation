package com.example.analytics;

import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.messaging.handler.annotation.SendTo;
import org.springframework.stereotype.Controller;

/**
 * Spring WebSocket (STOMP) entry point + return-side producer.
 *
 * <p>Two deterministic signals from one method:
 * <ul>
 *   <li>{@code @MessageMapping("/metrics.query")} &rarr; a {@code websocket}
 *       entry point on that destination.</li>
 *   <li>{@code @SendTo("/topic/metrics")} &rarr; a STOMP producer: the return
 *       value is brokered to that destination (broker-agnostic producer type).</li>
 * </ul>
 */
@Controller
public class AnalyticsWebSocketHandler {

    private final AnalyticsService analyticsService;

    public AnalyticsWebSocketHandler(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @MessageMapping("/metrics.query")
    @SendTo("/topic/metrics")
    public MetricSnapshot query(String metric) {
        return analyticsService.lookup(metric);
    }
}
