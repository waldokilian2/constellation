package com.example.analytics;

import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.annotation.WebServlet;
import org.springframework.stereotype.Component;

import java.io.IOException;

/**
 * Servlet API entry point.
 *
 * <p>Detected as a {@code servlet} because the class is annotated
 * {@code @WebServlet} and exposes {@code doX} verb methods. Each verb method
 * becomes one entry point on the declared {@code urlPatterns}; method_type is
 * the HTTP verb ("GET"/"POST").
 */
@Component
@WebServlet(urlPatterns = "/analytics/report")
public class AnalyticsServlet extends HttpServlet {

    private final AnalyticsService analyticsService;

    public AnalyticsServlet(AnalyticsService analyticsService) {
        this.analyticsService = analyticsService;
    }

    @Override
    protected void doGet(HttpServletRequest request, HttpServletResponse response) throws IOException {
        String metric = request.getParameter("metric");
        MetricSnapshot snapshot = analyticsService.lookup(metric);
        response.getWriter().write(snapshot.getMetric() + ":" + snapshot.getValue());
    }

    @Override
    protected void doPost(HttpServletRequest request, HttpServletResponse response) throws IOException {
        String orderId = request.getParameter("orderId");
        String metric = request.getParameter("metric");
        long value = Long.parseLong(request.getParameter("value"));
        MetricSnapshot snapshot = analyticsService.record(orderId, metric, value);
        response.getWriter().write(snapshot.getWindow());
    }
}
