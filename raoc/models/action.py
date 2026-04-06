"""Action definitions for RAOC execution plans.

ActionType enumerates every kind of step the execution agent can perform.
ActionObject is one step in a job's action plan, persisted to the actions table.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ActionType(str, Enum):
    """All step types an execution plan may contain."""

    FILE_READ   = 'file_read'
    FILE_WRITE  = 'file_write'
    FILE_BACKUP = 'file_backup'
    FILE_DELETE = 'file_delete'
    DIR_CREATE  = 'dir_create'
    CMD_EXECUTE = 'cmd_execute'
    CMD_INSPECT = 'cmd_inspect'
    SCREENSHOT  = 'screenshot'


class ActionObject(BaseModel):
    """One step in an approved execution plan.

    Stored in the actions table. status is updated by ExecutionAgent
    as each step runs. verification_result is set by VerificationAgent.
    """

    model_config = ConfigDict(use_enum_values=True)

    action_id:           str              = Field(default_factory=lambda: str(uuid4()))
    job_id:              str
    step_index:          int
    action_type:         ActionType
    risk_level:          str              # 'low', 'medium', or 'high'
    target_path:         str
    intent:              str
    command:             Optional[str]    = None
    change_summary:      Optional[str]    = None
    detected_format:     Optional[str]    = None  # transient; not persisted to DB
    write_strategy:      Optional[str]    = None  # transient; not persisted to DB
    text_blocks:         Optional[list]   = None  # transient; not persisted to DB
    status:              str              = 'pending'
    execution_output:    Optional[str]    = None
    verification_result: Optional[str]   = None

    created_at:          datetime         = Field(
                             default_factory=lambda: datetime.now(timezone.utc)
                         )
    completed_at:        Optional[datetime] = None
