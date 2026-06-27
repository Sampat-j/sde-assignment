from enum import Enum


class WorkflowState(str, Enum):
    RECEIVED = "received"

    RECORDING_PENDING = "recording_pending"
    RECORDING_UPLOADED = "recording_uploaded"

    LLM_PENDING = "llm_pending"
    LLM_RUNNING = "llm_running"
    LLM_COMPLETED = "llm_completed"

    SIGNAL_JOBS_PENDING = "signal_jobs_pending"
    LEAD_STAGE_PENDING = "lead_stage_pending"

    COMPLETED = "completed"

    FAILED = "failed"