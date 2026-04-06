"""Policy models — intentionally empty.

The multi-zone model (ZoneType, PolicyDecision, PolicyResult) was removed.
RAOC now enforces a single rule: everything inside ~/raoc_workspace/ is
allowed; everything outside is blocked immediately.

See raoc/substrate/zone_resolver.py for the implementation.
"""
