"""Task interpretation model for RAOC.

TaskObject is produced by IntakeAgent after Claude parses a raw message.
It carries the structured intent before Discovery and Planning begin.
"""

from typing import Optional

from pydantic import BaseModel


class TaskObject(BaseModel):
    """Structured interpretation of a user's natural language request.

    Produced by IntakeAgent via tool_use. Consumed by DiscoveryAgent
    and PlanningAgent. Not persisted directly — values are written back
    to the jobs table by IntakeAgent.
    """

    task_type:              str            # 'run_script', 'rewrite_file', 'query', or 'query_action'
    target_path:            Optional[str] = None
    instruction:            str
    risk_level:             str
    requires_clarification: bool           = False
    clarification_question: Optional[str] = None
    query_intent:           Optional[str] = None
    action_instruction:     Optional[str] = None
    implied_task_type:      Optional[str] = None
