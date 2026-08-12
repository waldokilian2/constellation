package com.example.analytics.grpc;

/**
 * gRPC response message (protoc stand-in).
 */
public class RecordMetricResponse {

    private final boolean accepted;
    private final long recorded;

    public RecordMetricResponse(boolean accepted, long recorded) {
        this.accepted = accepted;
        this.recorded = recorded;
    }

    public boolean isAccepted() {
        return accepted;
    }

    public long getRecorded() {
        return recorded;
    }
}
