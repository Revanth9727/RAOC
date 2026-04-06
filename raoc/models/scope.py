"""Scope and permission models for RAOC.

Defines the structured types used by zone_resolver, discovery, and
policy_agent to communicate scope decisions to the coordinator.

Permission rules:
    inside scope_root  → allowed (read, write, create, edit)
    outside scope_root → needs_approval (directory listing, opening, reading)
    forbidden paths    → always blocked, no approval option

Approvals are per-path, per-action, per-job.
"""

from dataclasses import dataclass, field
from pathlib import Path


# ── Forbidden prefixes (never accessible, no approval option) ────


FORBIDDEN_PREFIXES: list[str] = [
    '~/.ssh',
    '~/.aws',
    '~/.config',
    '~/Library/Keychains',
    '~/Library/Application Support',
    '/etc',
    '/System',
    '/usr/bin',
    '/usr/sbin',
    '/private/etc',
    '/private/var/root',
]


def expand_forbidden_prefixes() -> list[Path]:
    """Return FORBIDDEN_PREFIXES with ~ expanded to the real home directory."""
    return [Path(p).expanduser().resolve() for p in FORBIDDEN_PREFIXES]


# ── Structured result types ──────────────────────────────────────


@dataclass(frozen=True)
class NeedsPermission:
    """Returned when a path is outside scope_root and requires user approval.

    Used by discovery and policy_agent to signal the coordinator to pause
    and ask the user before proceeding.
    """

    reason: str           # e.g. 'path_outside_scope'
    path: str             # the absolute path that needs approval
    requested_action: str # 'read', 'write', 'list', 'inspect'


@dataclass(frozen=True)
class ScopeApproval:
    """Records a user's approval for a specific path, action, and job.

    Approvals are narrow:
      - exact path + exact action + exact job_id
      - no subtree expansion unless is_subtree is explicitly True
    """

    path: str
    action: str           # 'read', 'write', 'list'
    job_id: str
    is_subtree: bool = False  # True only if user explicitly approved a folder


    def covers(self, candidate_path: str, candidate_action: str) -> bool:
        """Return True if this approval covers the candidate path+action."""
        if self.action != candidate_action:
            return False
        if self.is_subtree:
            try:
                Path(candidate_path).resolve().relative_to(Path(self.path).resolve())
                return True
            except ValueError:
                return False
        return str(Path(candidate_path).resolve()) == str(Path(self.path).resolve())


@dataclass(frozen=True)
class PolicyDecision:
    """Structured result from policy_agent.review_plan().

    status values:
        'allowed'        — all actions inside scope_root, proceed
        'needs_approval' — one or more actions outside scope_root
        'forbidden'      — one or more actions hit a forbidden path
    """

    status: str        # 'allowed' | 'needs_approval' | 'forbidden'
    reason: str = ''   # human-readable explanation
    path: str = ''     # the offending path (empty if status == 'allowed')
    action: str = ''   # the offending action type (empty if status == 'allowed')


# ── Allowed-without-approval operations ──────────────────────────
#
# The following do NOT require approval even for paths outside scope_root:
#   - path normalisation / resolving (e.g. Path.resolve())
#   - comparing a path against scope_root or forbidden prefixes
#   - checking if a user-provided path string is valid
#
# These DO require approval for paths outside scope_root:
#   - directory listing (os.listdir, Path.iterdir, glob, rglob)
#   - file opening / reading (open, read, read_text)
#   - content reading (extraction, sampling)
#   - metadata beyond the exact user-provided path (stat on siblings, etc.)
