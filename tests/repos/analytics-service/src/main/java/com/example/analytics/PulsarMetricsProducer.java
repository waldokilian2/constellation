package com.example.analytics;

import org.apache.pulsar.client.api.PulsarTemplate;
import org.springframework.stereotype.Service;

/**
 * Publishes raw metrics to Apache Pulsar on the "metrics-deadletter" topic.
 *
 * <p>Deliberate GAP fixture: nothing in any repo consumes "metrics-deadletter",
 * so this surfaces as an <b>orphan producer</b> in {@code find_orphans} (a
 * channel produced but never consumed). Detected as a {@code pulsar-producer}
 * by the field type (PulsarTemplate) + {@code send} method.
 */
@Service
public class PulsarMetricsProducer {

    private final PulsarTemplate<byte[]> pulsarTemplate;

    public PulsarMetricsProducer(PulsarTemplate<byte[]> pulsarTemplate) {
        this.pulsarTemplate = pulsarTemplate;
    }

    public void emitRaw(String metric, long value) {
        String payload = metric + "=" + value;
        pulsarTemplate.send("metrics-deadletter", payload.getBytes());
    }
}
