"""Job record and status definitions for RAOC.

JobStatus tracks every stage a job passes through.
JobRecord is the single source of truth for a job's state.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    """All possible states a job can be in, in pipeline order."""

    RECEIVED          = 'received'
    UNDERSTANDING     = 'understanding'
    SEARCHING         = 'searching'
    CONFIRMING        = 'confirming'
    DISCOVERING       = 'discovering'
    PLANNING          = 'planning'
    AWAITING_APPROVAL = 'awaiting_approval'
    EXECUTING         = 'executing'
    VERIFYING         = 'verifying'
    REPORTING         = 'reporting'
    COMPLETED         = 'completed'
    FAILED            = 'failed'
    CANCELLED         = 'cancelled'
    BLOCKED           = 'blocked'


class JobRecord(BaseModel):
    """Persistent record for a single RAOC job.

    Created when a message arrives and updated at every pipeline stage.
    Stored in the jobs table. All agents read from and write to this record.
    """

    model_config = ConfigDict(use_enum_values=True)

    job_id:           str            = Field(default_factory=lambda: str(uuid4()))
    raw_request:      str
    task_type:        Optional[str]  = None
    target_path:      Optional[str]  = None
    status:           JobStatus      = JobStatus.RECEIVED
    created_at:       datetime       = Field(
                          default_factory=lambda: datetime.now(timezone.utc)
                      )
    updated_at:       datetime       = Field(
                          default_factory=lambda: datetime.now(timezone.utc)
                      )
    error_message:           Optional[str]  = None
    approval_granted:        Optional[bool] = None
    clarification_question:  Optional[str]  = None
    output_path:             Optional[str]  = None
    zip_source_path:         Optional[str]  = None
    query_intent:            Optional[str]  = None
    found_file_path:         Optional[str]  = None
    implied_task_type:       Optional[str]  = None
    action_instruction:      Optional[str]  = None
    scope_root:              Optional[str]  = None

    def update_status(self, new_status: JobStatus) -> None:
        """Set a new status and refresh updated_at to the current UTC time."""
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
