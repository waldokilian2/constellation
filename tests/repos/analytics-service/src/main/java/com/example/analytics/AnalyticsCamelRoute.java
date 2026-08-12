package com.example.analytics;

import org.apache.camel.builder.RouteBuilder;
import org.springframework.stereotype.Component;

/**
 * Apache Camel route: consumes order events from Kafka, enriches, and forwards
 * a summary to RabbitMQ.
 *
 * <p>Detected because the class extends {@code RouteBuilder} and declares
 * {@code from(...)}/{@code to(...)} endpoint URIs with broker schemes:
 * {@code kafka:order-events} becomes a {@code kafka-consumer} entry point and
 * {@code rabbitmq://.../analytics-summary} a {@code rabbitmq-producer}. The
 * {@code from} side links cross-repo to order-service's "order-events" topic.
 */
@Component
public class AnalyticsCamelRoute extends RouteBuilder {

    @Override
    public void configure() {
        from("kafka:order-events?groupId=analytics-camel")
            .to("rabbitmq://broker/analytics-summary?routingKey=metrics");
    }
}
