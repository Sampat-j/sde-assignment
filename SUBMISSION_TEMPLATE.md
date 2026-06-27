# Post-Call Processing Pipeline — Design Document

**Author:** Your Name
**Date:** June 27, 2026

---

# 1. Assumptions

The following assumptions were made while redesigning the pipeline:

1. Every interaction has a unique `interaction_id` and belongs to exactly one customer.
2. The webhook from Exotel must respond within 5 seconds, so all heavy processing must happen asynchronously.
3. Transcript text is available immediately after call completion.
4. Audio recordings are optional for LLM analysis because the LLM operates on transcript text.
5. Exotel recording availability is eventually consistent and may take between 10–90 seconds.
6. LLM providers enforce hard Requests Per Minute (RPM) and Tokens Per Minute (TPM) limits.
7. Multiple customers may execute large outbound campaigns simultaneously.
8. Celery and Redis remain the background processing infrastructure.
9. Dashboard users expect near real-time analysis but can tolerate minor delays during high system load.
10. Signal jobs (CRM updates, WhatsApp, callbacks) require completed LLM analysis before execution.

---

# 2. Problem Diagnosis

The original pipeline worked correctly at low scale but exhibited several issues under heavy load.

The webhook correctly returned immediately, but the downstream pipeline suffered from architectural bottlenecks.

Major issues included:

* Recording upload blocked LLM analysis despite having no dependency.
* Fixed 45-second recording wait wasted processing time.
* Signal jobs executed before LLM analysis completed, resulting in empty payloads.
* Short transcript detection only existed in the API layer and was not enforced inside background processing.
* No global LLM rate limiting existed, causing provider 429 errors.
* No customer-level fairness meant one customer could consume all available LLM capacity.
* Limited auditability made production debugging difficult.
* Missing recording uploads were silently ignored.

These problems become severe once processing reaches tens of thousands of concurrent interactions.

---

# 3. Architecture Overview

```
                  Exotel Webhook
                        │
                        ▼
          Update Interaction Status
                        │
                        ▼
          Enqueue Celery Background Task
                        │
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
 Recording Polling             LLM Scheduler
   (retry/backoff)                  │
          │                         │
          └─────────────┬───────────┘
                        ▼
                 LLM Analysis
                        │
                        ▼
          Update Interaction Metadata
                        │
                        ▼
                Trigger Signal Jobs
                        │
                        ▼
                Update Lead Stage
                        │
                        ▼
                  Audit Completed
```

### Key Design Decisions

1. Keep webhook latency below 5 seconds.
2. Execute recording retrieval and LLM analysis concurrently.
3. Move all business processing into Celery.
4. Prevent downstream systems from receiving incomplete analysis.
5. Add audit logging across every processing stage.
6. Introduce centralized LLM scheduling for rate-limit management.

---

# 4. Rate Limit Management

A centralized LLM scheduler was introduced before every LLM request.

Instead of allowing every worker to call the provider immediately, each request first reserves estimated tokens.

## How usage is tracked

* Estimated tokens per request
* Current Tokens Per Minute
* Current Requests Per Minute
* Customer currently consuming capacity

Redis maintains rolling counters that expire automatically every minute.

## Processing Decision

If capacity exists:

* Execute immediately.

If capacity is exhausted:

* Queue the request until sufficient tokens become available.

## Recovery

Instead of generating provider 429 responses, requests wait inside the scheduler.

Workers never repeatedly retry failed rate-limited requests.

---

# 5. Per-Customer Token Budgeting

Available TPM is divided among active customers.

Each customer receives a guaranteed token allocation.

Benefits:

* One customer's campaign cannot starve another.
* Predictable processing latency.
* Fair resource utilization.

If a customer exceeds its allocation:

* Additional requests remain queued.
* Previously allocated customers continue processing normally.

Unused capacity is redistributed dynamically to waiting customers.

---

# 6. Differentiated Processing

Not every call deserves identical processing priority.

Examples:

High Priority

* Appointment confirmed
* Callback requested
* Payment discussed
* Sales opportunity identified

Normal Priority

* Information requests
* Product inquiries

Low Priority

* Wrong number
* Immediate disconnect
* Spam
* Network failure

Short transcripts skip expensive LLM processing entirely.

Priority is determined from transcript characteristics and call outcome.

---

# 7. Recording Pipeline

The fixed 45-second delay was replaced with configurable polling using exponential backoff.

Example:

Attempt 1 → 5 seconds

Attempt 2 → 10 seconds

Attempt 3 → 20 seconds

Attempt 4 → 40 seconds

Attempt 5 → 60 seconds

Polling stops immediately when the recording becomes available.

Every failed attempt is logged.

If recording retrieval ultimately fails:

* interaction_id
* call_sid
* retry count
* last error
* elapsed time

are recorded for operational debugging.

---

# 8. Reliability & Durability

Reliability improvements include:

* Celery late acknowledgements
* Configurable retry policies
* Audit logging
* Retry queue
* Idempotent interaction processing

Processing state is preserved across worker failures.

Short transcript checks also exist inside background processing to prevent duplicate LLM execution after retries.

---

# 9. Auditability & Observability

Each processing stage generates structured audit events.

Typical events include:

* INTERACTION_ENDED
* POSTCALL_TASK_ENQUEUED
* RECORDING_UPLOAD_STARTED
* RECORDING_UPLOAD_COMPLETED
* LLM_ANALYSIS_STARTED
* LLM_ANALYSIS_COMPLETED
* SIGNAL_JOBS_STARTED
* LEAD_STAGE_UPDATE_STARTED
* POSTCALL_PROCESSING_COMPLETED
* POSTCALL_PROCESSING_FAILED

### Standard Log Fields

Every event includes:

* interaction_id
* customer_id
* campaign_id
* timestamp
* event name
* latency (when applicable)
* tokens_used (when applicable)
* retry_attempt (if applicable)

### Alert Conditions

* Recording unavailable after maximum retries
* Celery retries exceeded
* Scheduler queue growth
* LLM provider failures
* Excessive token consumption
* Signal job failures

---

# 10. Data Model

```sql
ALTER TABLE interactions
ADD COLUMN correlation_id UUID,
ADD COLUMN processing_status VARCHAR(50),
ADD COLUMN recording_status VARCHAR(50),
ADD COLUMN recording_s3_key TEXT,
ADD COLUMN llm_started_at TIMESTAMP,
ADD COLUMN llm_completed_at TIMESTAMP;

CREATE TABLE audit_events (
    id UUID PRIMARY KEY,
    interaction_id UUID,
    customer_id TEXT,
    campaign_id TEXT,
    event TEXT,
    created_at TIMESTAMP,
    metadata JSONB
);

CREATE INDEX idx_audit_interaction
ON audit_events(interaction_id);

CREATE INDEX idx_audit_customer
ON audit_events(customer_id);
```

---

# 11. Security

Sensitive data includes:

* Customer phone numbers
* Conversation transcripts
* Recording URLs
* API keys
* LLM prompts
* Customer metadata

Security measures:

* TLS for all external communication
* Encryption at rest
* IAM-based S3 permissions
* Secrets stored outside source code
* Role-based access to audit logs
* Redaction of sensitive fields where appropriate

---

# 12. API Interface

The API contract remains unchanged.

```
POST /session/{session_id}/interaction/{interaction_id}/end
```

Maintaining the existing contract avoids breaking Exotel integrations.

All improvements occur internally within asynchronous processing.

---

# 13. Trade-offs & Alternatives Considered

| Option                | Why Considered              | Decision                               |
| --------------------- | --------------------------- | -------------------------------------- |
| Fixed recording delay | Simple implementation       | Replaced with polling                  |
| Sequential processing | Easier to understand        | Parallel execution chosen              |
| Direct LLM calls      | Lower implementation effort | Scheduler prevents rate-limit failures |
| Multiple queues       | Better prioritization       | Deferred for future scalability        |
| API-side signal jobs  | Lower latency               | Moved after completed analysis         |

---

# 14. Known Weaknesses

Remaining limitations include:

* Celery still uses a single processing queue.
* Customer priority is static.
* Scheduler currently estimates tokens before actual usage.
* Retry queue and Celery retries could still be unified.
* Recording and LLM processing still share the same Celery task.

---

# 15. What I Would Do With More Time

1. Introduce customer-specific Celery queues.
2. Implement priority queues based on call importance.
3. Replace estimated token accounting with actual provider usage.
4. Build a real-time processing dashboard.
5. Add dead-letter queues for permanently failed interactions.
6. Implement automated replay tooling for failed workflows.
7. Add OpenTelemetry distributed tracing across the entire pipeline.
