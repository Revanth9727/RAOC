"""PolicyAgent — evaluates every ActionObject before execution.

Reads all actions for a job, evaluates each against the zone model,
stamps policy_decision / policy_reason / target_zone on each action row,
writes audit entries, and returns a list[PolicyResult].

State changes (BLOCKED status, gateway messages) are the coordinator's
responsibility — this agent only stamps and returns.
"""

import logging

from raoc.db import queries
from raoc.models.action import ActionObject, ActionType
from raoc.models.policy import PolicyDecision, PolicyResult, ZoneType
from raoc.substrate.exceptions import AmbiguousZoneError
from raoc.substrate.zone_resolver import ZoneResolver

logger = logging.getLogger(__name__)

# Action types that are read-only — safe in read_only zones
_READ_TYPES = {
    ActionType.FILE_READ,
    ActionType.CMD_INSPECT,
    ActionType.SCREENSHOT,
}

# Action types that write — blocked in read_only zones
_WRITE_TYPES = {
    ActionType.FILE_WRITE,
    ActionType.FILE_BACKUP,
    ActionType.FILE_DELETE,
    ActionType.DIR_CREATE,
}


class PolicyAgent:
    """Evaluates every planned action against the zone model.

    Does not call Claude. Does not touch job status. Returns results only.
    """

    def __init__(self, db, zone_resolver: ZoneResolver, llm=None) -> None:
        """Store db engine and zone resolver."""
        self.db = db
        self.zone_resolver = zone_resolver

    def review_plan(self, job_id: str) -> list[PolicyResult]:
        """Evaluate every action for a job and stamp policy fields on each row.

        Returns the full list of PolicyResult, one per action.
        Each action in the database is updated with policy_decision, policy_reason,
        and target_zone before this method returns.
        """
        actions = queries.get_actions_for_job(job_id, engine=self.db)
        results: list[PolicyResult] = []

        for action in actions:
            result = self._evaluate_action(action)
            queries.update_action_policy(
                action_id=action.action_id,
                decision=result.decision.value,
                reason=result.reason,
                zone=result.zone.value,
                engine=self.db,
            )
            queries.write_audit(
                job_id,
                'policy_decision',
                detail=f"step {action.step_index}: {result.decision.value} — {result.reason}",
                engine=self.db,
            )
            results.append(result)
            logger.info(
                "Policy: job=%s step=%d action=%s → %s",
                job_id, action.step_index, action.action_type, result.decision.value,
            )

        return results

    def _evaluate_action(self, action: ActionObject) -> PolicyResult:
        """Return the PolicyResult for one action using the three-step decision table.

        Step 1 — Forbidden check (always first): if zone is forbidden → blocked.
        Step 2 — Capability override: if CMD_EXECUTE → approval_required.
        Step 3 — Zone table: safe_workspace/read_only/restricted rules.
        Step 4 — Judgment zone: AmbiguousZoneError or zip target.
        """
        from pathlib import Path

        target = action.target_path or ''
        action_type_str = (
            action.action_type.value
            if hasattr(action.action_type, 'value')
            else str(action.action_type)
        )

        # Resolve zone (may raise AmbiguousZoneError → judgment_zone)
        try:
            zone = self.zone_resolver.resolve(Path(target))
        except AmbiguousZoneError:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.JUDGMENT_ZONE,
                zone=ZoneType.RESTRICTED,  # fallback zone for model validity
                reason=(
                    f"{target} matches entries in two different zones at equal specificity. "
                    f"Policy cannot determine which zone applies — review before approving."
                ),
            )

        # Step 1: Forbidden check (always wins)
        if zone == ZoneType.FORBIDDEN:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.BLOCKED,
                zone=zone,
                reason=(
                    f"{target} is in the forbidden zone. "
                    f"This path cannot be automated. This is a permanent restriction "
                    f"that cannot be bypassed."
                ),
            )

        # Step 2: CMD_EXECUTE capability override
        if action_type_str == ActionType.CMD_EXECUTE.value:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.APPROVAL_REQUIRED,
                zone=zone,
                reason=(
                    f"Script execution always requires approval regardless of location. "
                    f"Target: {target}."
                ),
            )

        # Step 3: Zone table
        if zone == ZoneType.SAFE_WORKSPACE:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.AUTO_APPROVED,
                zone=zone,
                reason=f"{target} is in the safe workspace — auto-approved.",
            )

        if zone == ZoneType.READ_ONLY:
            action_enum = None
            try:
                action_enum = ActionType(action_type_str)
            except ValueError:
                pass

            if action_enum in _READ_TYPES:
                return PolicyResult(
                    action_id=action.action_id,
                    decision=PolicyDecision.AUTO_APPROVED,
                    zone=zone,
                    reason=f"{target} is read-only and this is a read action — auto-approved.",
                )
            else:
                return PolicyResult(
                    action_id=action.action_id,
                    decision=PolicyDecision.BLOCKED,
                    zone=zone,
                    reason=(
                        f"{target} is in a read-only zone. "
                        f"Write actions are blocked in read-only zones."
                    ),
                )

        if zone == ZoneType.RESTRICTED:
            return PolicyResult(
                action_id=action.action_id,
                decision=PolicyDecision.APPROVAL_REQUIRED,
                zone=zone,
                reason=f"{target} is in a restricted zone — requires your approval.",
            )

        # Fallback (should not reach here with valid zone values)
        return PolicyResult(
            action_id=action.action_id,
            decision=PolicyDecision.JUDGMENT_ZONE,
            zone=zone,
            reason=f"Unhandled zone state for {target} — review before approving.",
        )
