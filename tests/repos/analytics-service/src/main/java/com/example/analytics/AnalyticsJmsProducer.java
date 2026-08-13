package com.example.analytics;

import org.springframework.jms.core.JmsTemplate;
import org.springframework.stereotype.Service;

/**
 * Sends metrics to a JMS queue ("metrics-jobs").
 *
 * <p>Detected as a {@code jms-producer} by the field type (JmsTemplate) and
 * the {@code convertAndSend} method. The channel is the first argument.
 */
@Service
public class AnalyticsJmsProducer {

    private final JmsTemplate jmsTemplate;

    public AnalyticsJmsProducer(JmsTemplate jmsTemplate) {
        this.jmsTemplate = jmsTemplate;
    }

    public void broadcast(MetricSnapshot snapshot) {
        jmsTemplate.convertAndSend("metrics-jobs", snapshot);
    }
}
