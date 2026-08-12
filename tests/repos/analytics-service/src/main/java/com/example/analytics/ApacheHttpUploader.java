package com.example.analytics;

import org.apache.http.client.methods.HttpPost;
import org.apache.http.impl.client.CloseableHttpClient;
import org.springframework.stereotype.Service;

/**
 * Sync HTTP producer via Apache HttpComponents.
 *
 * <p>Detected as an {@code http-call} producer: the field type is
 * {@code CloseableHttpClient} and the method is {@code execute}. The verb and
 * URL are read from the inline {@code new HttpPost("…")} request object passed
 * to {@code execute}. HTTP callers are resolved in the path domain (not the
 * message-channel gap view).
 */
@Service
public class ApacheHttpUploader {

    private final CloseableHttpClient httpClient;

    public ApacheHttpUploader(CloseableHttpClient httpClient) {
        this.httpClient = httpClient;
    }

    public void upload(MetricSnapshot snapshot) {
        try {
            HttpPost request = new HttpPost("http://data-lake/api/uploads/metrics");
            httpClient.execute(request);
        } catch (Exception ignored) {
        }
    }
}
