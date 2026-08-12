package com.example.analytics;

import jakarta.jws.WebMethod;
import jakarta.jws.WebService;
import org.springframework.stereotype.Service;

/**
 * JAX-WS SOAP service entry point.
 *
 * <p>Detected as a {@code soap-service} because the class is annotated
 * {@code @WebService} and the methods carry {@code @WebMethod}. The channel is
 * the operation name (from {@code operationName}, here falling back to the
 * method name) and method_type is "SOAP".
 */
@Service
@WebService
public class ReportingWebService {

    private final AnalyticsService analyticsService;

    public ReportingWebService(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @WebMethod
    public MetricSnapshot fetchReport(String metric) {
        return analyticsService.lookup(metric);
    }

    @WebMethod(operationName = "BuildReport")
    public MetricSnapshot buildReport(String orderId, String metric, long value) {
        return analyticsService.aggregate(orderId, metric, value);
    }
}
