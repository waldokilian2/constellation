package com.example.analytics.grpc;

/**
 * gRPC request message (protoc stand-in).
 */
public class RecordMetricRequest {

    private final String orderId;
    private final String metric;
    private final long value;

    public RecordMetricRequest(String orderId, String metric, long value) {
        this.orderId = orderId;
        this.metric = metric;
        this.value = value;
    }

    public String getOrderId() {
        return orderId;
    }

    public String getMetric() {
        return metric;
    }

    public long getValue() {
        return value;
    }
}
