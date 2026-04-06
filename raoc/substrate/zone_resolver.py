"""ZoneResolver — enforces per-job scope boundaries.

Three access levels:
    inside scope_root  → 'allowed' (no approval needed)
    outside scope_root → 'needs_approval' (user must approve)
    forbidden path     → 'forbidden' (always blocked)

Allowed without approval even for outside-scope paths:
    path normalisation, forbidden-prefix check, scope comparison.

Requires approval for outside-scope paths:
    directory listing, file opening, content reading, broad search.
"""

import logging
from pathlib import Path

from raoc import config
from raoc.models.scope import (
    NeedsPermission,
    ScopeApproval,
    expand_forbidden_prefixes,
)
from raoc.substrate.exceptions import ScopeViolationError

logger = logging.getLogger(__name__)


class ZoneResolver:
    """Resolves whether a path is inside scope, outside scope, or forbidden.

    Uses a per-job scope_root (not the whole workspace) as the trusted zone.
    """

    def __init__(self) -> None:
        self._forbidden = expand_forbidden_prefixes()

    # ── Core checks (no I/O, safe to call anytime) ───────────────

    def is_forbidden(self, path: Path) -> bool:
        """Return True if path matches any forbidden prefix."""
        resolved = path.resolve()
        return any(
            resolved == fp or self._is_descendant(resolved, fp)
            for fp in self._forbidden
        )

    def is_inside_scope(self, path: Path, scope_root: Path) -> bool:
        """Return True if path is inside scope_root."""
        try:
            path.resolve().relative_to(scope_root.resolve())
            return True
        except ValueError:
            return False

    def is_inside_workspace(self, path: Path) -> bool:
        """Return True if path is inside config.WORKSPACE (broad check)."""
        try:
            path.resolve().relative_to(config.WORKSPACE.resolve())
            return True
        except ValueError:
            return False

    # ── Main access check ────────────────────────────────────────

    def check_access(
        self,
        path: Path,
        scope_root: Path,
        action: str,
        approved: list[ScopeApproval] | None = None,
    ) -> str:
        """Return 'allowed', 'needs_approval', or 'forbidden'.

        Checks in order:
            1. Forbidden prefixes → 'forbidden'
            2. Inside scope_root → 'allowed'
            3. Previously approved for this path+action → 'allowed'
            4. Otherwise → 'needs_approval'
        """
        resolved = path.resolve()

        if self.is_forbidden(resolved):
            return 'forbidden'

        if self.is_inside_scope(resolved, scope_root):
            return 'allowed'

        # Check if user already approved this path+action
        if approved:
            for approval in approved:
                if approval.covers(str(resolved), action):
                    return 'allowed'

        return 'needs_approval'

    # ── Legacy compatibility ─────────────────────────────────────

    def assert_allowed(self, path: Path) -> None:
        """Raise ScopeViolationError if path is outside workspace.

        Used as a safety net in execution. Uses the broad workspace check.
        """
        if self.is_forbidden(path):
            raise ScopeViolationError(
                f"'{path}' is a forbidden system path. "
                f"RAOC cannot access this location."
            )
        if not self.is_inside_workspace(path):
            raise ScopeViolationError(
                f"'{path}' is outside the workspace. "
                f"RAOC only operates inside {config.WORKSPACE}. "
                f"Copy the file into your workspace first, then ask again."
            )

    # ── Internal ─────────────────────────────────────────────────

    @staticmethod
    def _is_descendant(child: Path, parent: Path) -> bool:
        """Return True if child is a descendant of parent."""
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False
