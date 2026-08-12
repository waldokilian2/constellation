package com.example.analytics;

import io.nats.client.Connection;
import org.springframework.stereotype.Service;

/**
 * Publishes metrics to a NATS subject ("metrics.snapshot").
 *
 * <p>Detected as a {@code nats-producer} by the field type (Connection — the
 * nats.java {@code Connection} interface) + the {@code publish} method; the
 * subject is the first argument.
 */
@Service
public class NatsMetricsPublisher {

    private final Connection natsConnection;

    public NatsMetricsPublisher(Connection natsConnection) {
        this.natsConnection = natsConnection;
    }

    public void publish(MetricSnapshot snapshot) {
        byte[] body = (snapshot.getMetric() + ":" + snapshot.getValue()).getBytes();
        natsConnection.publish("metrics.snapshot", body);
    }
}
