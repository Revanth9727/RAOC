"""Policy models for RAOC zone enforcement.

ZoneType enumerates the four zones a filesystem path can belong to.
PolicyDecision enumerates the four outcomes the policy engine can produce.
PolicyResult carries the decision for one ActionObject.
"""

from enum import Enum

from pydantic import BaseModel


class ZoneType(str, Enum):
    """Four zones that define what RAOC may do in a given path."""

    SAFE_WORKSPACE = 'safe_workspace'
    READ_ONLY      = 'read_only'
    RESTRICTED     = 'restricted'
    FORBIDDEN      = 'forbidden'


class PolicyDecision(str, Enum):
    """Four possible outcomes from the policy engine for one action."""

    AUTO_APPROVED      = 'auto_approved'
    APPROVAL_REQUIRED  = 'approval_required'
    BLOCKED            = 'blocked'
    JUDGMENT_ZONE      = 'judgment_zone'


class PolicyResult(BaseModel):
    """Policy engine verdict for one ActionObject.

    reason is plain English and includes the path — ready to show to the user
    if decision is blocked or judgment_zone.
    """

    action_id: str
    decision:  PolicyDecision
    zone:      ZoneType
    reason:    str
