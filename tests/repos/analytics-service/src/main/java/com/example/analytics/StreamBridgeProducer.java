package com.example.analytics;

import org.springframework.cloud.stream.function.StreamBridge;
import org.springframework.stereotype.Service;

/**
 * Bridges a metric out via Spring Cloud Stream ({@code StreamBridge}).
 *
 * <p>Detected by the field type (StreamBridge) + {@code send} method. The
 * producer type is broker-agnostic (links by channel name only). The
 * "metrics-out" binding has no consumer → a second orphan-producer signal.
 */
@Service
public class StreamBridgeProducer {

    private final StreamBridge streamBridge;

    public StreamBridgeProducer(StreamBridge streamBridge) {
        this.streamBridge = streamBridge;
    }

    public void send(MetricSnapshot snapshot) {
        streamBridge.send("metrics-out", snapshot);
    }
}
