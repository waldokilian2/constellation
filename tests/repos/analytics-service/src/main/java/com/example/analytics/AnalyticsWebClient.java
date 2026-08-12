package com.example.analytics;

import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * Fluent HTTP client (Spring WebClient.Builder) that pushes a snapshot to an
 * external dashboard.
 *
 * <p>The verb and URL are split across a chained call
 * ({@code builder.build().post().uri(...)}), which the per-invocation match
 * cannot see. The fluent-chain detector walks inward from the {@code .uri(url)}
 * call, records the {@code post()} verb, notes {@code .build()}, and confirms
 * the root field type is {@code Builder} → an {@code http-call} producer.
 */
@Service
public class AnalyticsWebClient {

    private final WebClient.Builder webClientBuilder;

    public AnalyticsWebClient(WebClient.Builder webClientBuilder) {
        this.webClientBuilder = webClientBuilder;
    }

    public void pushSnapshot(MetricSnapshot snapshot) {
        webClientBuilder.build().post()
            .uri("http://dashboard/api/metrics/snapshot")
            .bodyValue(snapshot);
    }
}
