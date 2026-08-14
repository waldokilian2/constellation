package com.example.legacy;

import org.springframework.jms.core.JmsTemplate;
import org.springframework.stereotype.Service;

/**
 * Dispatches batch jobs onto the "legacy-jobs" queue.
 *
 * <p>Orphan channel fixture: no consumer exists for "legacy-jobs" anywhere
 * in the project, so this surfaces as an orphan producer (amber gap ring)
 * while the repo itself stays isolated — no cross-repo link is formed.
 */
@Service
public class LegacyJobDispatcher {

    private final JmsTemplate jmsTemplate;

    public LegacyJobDispatcher(JmsTemplate jmsTemplate) {
        this.jmsTemplate = jmsTemplate;
    }

    public void dispatch(String jobName) {
        jmsTemplate.convertAndSend("legacy-jobs", jobName);
    }
}
