package com.example.analytics;

import com.example.analytics.grpc.AnalyticsServiceImplBase;
import com.example.analytics.grpc.RecordMetricRequest;
import com.example.analytics.grpc.RecordMetricResponse;
import io.grpc.stub.StreamObserver;
import org.springframework.stereotype.Service;

/**
 * gRPC service entry point.
 *
 * <p>Detected as a {@code grpc-service} because the class extends a generated
 * {@code *ImplBase} (service name = "AnalyticsService"), the methods carry
 * {@code @Override}, and a parameter type contains {@code StreamObserver}.
 * The channel is {@code /AnalyticsService/&lt;method&gt;} and method_type is
 * "GRPC".
 */
@Service
public class AnalyticsGrpcService extends AnalyticsServiceImplBase {

    private final AnalyticsService analyticsService;

    public AnalyticsGrpcService(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @Override
    public void recordMetric(RecordMetricRequest request,
                             StreamObserver<RecordMetricResponse> responseObserver) {
        MetricSnapshot snapshot = analyticsService.record(
            request.getOrderId(), request.getMetric(), request.getValue());
        responseObserver.onNext(new RecordMetricResponse(true, snapshot.getValue()));
        responseObserver.onCompleted();
    }
}
