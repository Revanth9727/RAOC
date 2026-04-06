"""PolicyAgent — validates action paths against the job's scope_root.

Returns a structured PolicyDecision for each plan review:
    'allowed'        — all actions inside scope_root, proceed
    'needs_approval' — one or more actions outside scope_root
    'forbidden'      — one or more actions hit a forbidden path
"""

import logging
from pathlib import Path

from raoc import config
from raoc.db import queries
from raoc.models.action import ActionObject, ActionType
from raoc.models.job import JobStatus
from raoc.models.scope import PolicyDecision, ScopeApproval
from raoc.substrate.zone_resolver import ZoneResolver

logger = logging.getLogger(__name__)


class PolicyAgent:
    """Validates that every action targets a path inside the job's scope_root.

    Returns a PolicyDecision with status 'allowed', 'needs_approval', or
    'forbidden'. The coordinator uses this to decide whether to proceed,
    ask the user, or block.
    """

    def __init__(self, db, zone_resolver: ZoneResolver) -> None:
        """Store db engine and zone resolver."""
        self.db = db
        self.zone_resolver = zone_resolver

    def review_plan(
        self,
        job_id: str,
        actions: list[ActionObject],
        approved: list[ScopeApproval] | None = None,
    ) -> PolicyDecision:
        """Review all actions against the job's scope_root.

        Returns PolicyDecision with:
            'allowed'        — all paths inside scope_root (or previously approved)
            'needs_approval' — at least one path outside scope_root
            'forbidden'      — at least one path is a forbidden system path
        """
        job = queries.get_job(job_id, engine=self.db)
        scope_root = Path(job.scope_root) if job.scope_root else config.WORKSPACE

        for action in actions:
            if not action.target_path:
                continue

            # SCREENSHOT has no local file path — always allowed
            if action.action_type == ActionType.SCREENSHOT:
                continue

            target = Path(action.target_path)
            action_type_str = (
                action.action_type.value
                if hasattr(action.action_type, 'value')
                else str(action.action_type)
            )
            # Map action_type to a simple verb for scope tracking
            scope_action = 'write' if 'write' in action_type_str else 'read'

            access = self.zone_resolver.check_access(
                target, scope_root, scope_action, approved,
            )

            if access == 'forbidden':
                reason = (
                    f"'{action.target_path}' is a forbidden system path. "
                    f"RAOC cannot access this location."
                )
                queries.update_job_status(
                    job_id, JobStatus.BLOCKED, error=reason, engine=self.db,
                )
                queries.write_audit(job_id, 'job_blocked', detail=reason, engine=self.db)
                return PolicyDecision(
                    status='forbidden',
                    reason=reason,
                    path=action.target_path,
                    action=scope_action,
                )

            if access == 'needs_approval':
                return PolicyDecision(
                    status='needs_approval',
                    reason=f"'{action.target_path}' is outside the current allowed directory.",
                    path=action.target_path,
                    action=scope_action,
                )

        return PolicyDecision(status='allowed')
