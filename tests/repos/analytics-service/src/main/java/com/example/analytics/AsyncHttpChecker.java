package com.example.analytics;

import org.asynchttpclient.AsyncHttpClient;
import org.springframework.stereotype.Service;

/**
 * Sync HTTP producer via the async-http-client library ( Ning).
 *
 * <p>Detected as an {@code http-call} producer: the field type is
 * {@code AsyncHttpClient} and the verb is encoded in the {@code prepareGet}
 * method name. The URL is the first argument.
 */
@Service
public class AsyncHttpChecker {

    private final AsyncHttpClient asyncHttpClient;

    public AsyncHttpChecker(AsyncHttpClient asyncHttpClient) {
        this.asyncHttpClient = asyncHttpClient;
    }

    public void pingHealth() {
        asyncHttpClient.prepareGet("http://dashboard/api/health").execute();
    }
}
