package com.example.analytics;

import org.springframework.stereotype.Service;

/**
 * Core analytics orchestration. Drives a deep, resolvable call tree: each
 * method fans out to the repository and across the full broker mix
 * (Kafka/JMS/Pulsar/NATS/StreamBridge/HTTP), all resolving to concrete in-repo
 * definitions ({@code EXTRACTED} confidence edges).
 *
 * <p>Every Tier 1/2 entry point (GraphQL, gRPC, SOAP, Servlet, Cloud Function,
 * SQS, main, …) funnels through this one resolvable business layer, so the
 * broker producers are reachable from entry points rather than dead leaves.
 * The sole deliberate dead code is {@link LegacyReportFormatter}.
 */
@Service
public class AnalyticsService {

    private final AnalyticsRepository repository;
    private final AnalyticsEventProducer eventProducer;
    private final AnalyticsEventPublisher eventPublisher;
    private final AnalyticsJmsProducer jmsProducer;
    private final PulsarMetricsProducer pulsarProducer;
    private final NatsMetricsPublisher natsPublisher;
    private final StreamBridgeProducer streamProducer;
    private final AnalyticsWebClient webClient;
    private final ApacheHttpUploader apacheUploader;
    private final AsyncHttpChecker asyncChecker;

    public AnalyticsService(AnalyticsRepository repository,
                            AnalyticsEventProducer eventProducer,
                            AnalyticsEventPublisher eventPublisher,
                            AnalyticsJmsProducer jmsProducer,
                            PulsarMetricsProducer pulsarProducer,
                            NatsMetricsPublisher natsPublisher,
                            StreamBridgeProducer streamProducer,
                            AnalyticsWebClient webClient,
                            ApacheHttpUploader apacheUploader,
                            AsyncHttpChecker asyncChecker) {
        this.repository = repository;
        this.eventProducer = eventProducer;
        this.eventPublisher = eventPublisher;
        this.jmsProducer = jmsProducer;
        this.pulsarProducer = pulsarProducer;
        this.natsPublisher = natsPublisher;
        this.streamProducer = streamProducer;
        this.webClient = webClient;
        this.apacheUploader = apacheUploader;
        this.asyncChecker = asyncChecker;
    }

    public MetricSnapshot record(String orderId, String metric, long value) {
        MetricSnapshot snapshot = repository.increment(metric, value);
        repository.save(snapshot);
        eventProducer.publish(orderId, snapshot);
        eventPublisher.publishMetricComputed(snapshot);
        pulsarProducer.emitRaw(metric, value);
        return snapshot;
    }

    public MetricSnapshot lookup(String metric) {
        asyncChecker.pingHealth();
        return repository.find(metric);
    }

    public MetricSnapshot aggregate(String orderId, String metric, long value) {
        MetricSnapshot snapshot = repository.increment(metric, value);
        jmsProducer.broadcast(snapshot);
        natsPublisher.publish(snapshot);
        streamProducer.send(snapshot);
        webClient.pushSnapshot(snapshot);
        apacheUploader.upload(snapshot);
        repository.save(snapshot);
        return snapshot;
    }
}
