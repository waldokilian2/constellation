package com.example.analytics.grpc;

import io.grpc.stub.StreamObserver;

/**
 * Generated gRPC service base (stand-in for the protoc-generated class).
 *
 * <p>Real projects get this from {@code protoc}; it is included here so the
 * {@code extends AnalyticsServiceImplBase} supertype in
 * {@link com.example.analytics.AnalyticsGrpcService} resolves. Its
 * {@code recordMetric} is an abstract contract (no body) — dispatched
 * dynamically by the gRPC runtime — so it is never flagged as dead code.
 */
public abstract class AnalyticsServiceImplBase {

    public abstract void recordMetric(RecordMetricRequest request,
                                      StreamObserver<RecordMetricResponse> responseObserver);
}
