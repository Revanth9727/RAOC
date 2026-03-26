"""ZoneResolver — maps filesystem paths to ZoneType values.

Reads zone_config.yaml on init and caches it. Never re-reads at runtime.
Four hard-coded overrides cannot be changed via config:
  - config.WORKSPACE       → safe_workspace (always)
  - ~/.ssh                 → forbidden (always)
  - ~/Library/Keychains    → forbidden (always)
  - ~/.aws                 → forbidden (always)
  - ~/.config              → forbidden (always)

For all other paths, the most specific (longest) matching prefix wins.
Unmatched paths default to restricted.
Tie (two entries at equal depth) raises AmbiguousZoneError.
Missing config file logs a warning and treats all unmatched paths as restricted.
"""

import logging
from pathlib import Path
from typing import Optional

from raoc import config
from raoc.models.policy import ZoneType
from raoc.substrate.exceptions import AmbiguousZoneError

logger = logging.getLogger(__name__)

# Hard-coded forbidden paths — cannot be overridden by zone_config.yaml
_HARDCODED_FORBIDDEN: list[Path] = [
    Path.home() / '.ssh',
    Path.home() / 'Library' / 'Keychains',
    Path.home() / '.aws',
    Path.home() / '.config',
]


class ZoneResolver:
    """Resolves a filesystem path to its ZoneType.

    Instantiate once at startup; call resolve() for each path.
    """

    def __init__(self, config_path: Path) -> None:
        """Load zone_config.yaml from config_path.

        If the file is missing, logs a warning and uses safe defaults
        (all unmatched paths → restricted).
        """
        self._zones: dict[str, ZoneType] = {}  # resolved_path_str → ZoneType
        self._config_loaded = False
        self._load(config_path)

    def _load(self, config_path: Path) -> None:
        """Parse zone_config.yaml into self._zones."""
        if not config_path.exists():
            logger.warning(
                "zone_config.yaml not found at %s — all unmatched paths will be treated as "
                "restricted. Create zone_config.yaml at the project root to configure zones.",
                config_path,
            )
            return

        try:
            import yaml
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("Failed to parse zone_config.yaml (%s): %s", config_path, exc)
            return

        zone_map = {
            'safe_workspace': ZoneType.SAFE_WORKSPACE,
            'read_only':      ZoneType.READ_ONLY,
            'restricted':     ZoneType.RESTRICTED,
            'forbidden':      ZoneType.FORBIDDEN,
        }
        for zone_name, zone_type in zone_map.items():
            for raw_path in (data.get(zone_name) or []):
                resolved = Path(raw_path).expanduser().resolve()
                key = str(resolved)
                if key in self._zones and self._zones[key] != zone_type:
                    logger.warning(
                        "zone_config.yaml: path %s appears in both '%s' and '%s' zones; "
                        "keeping '%s' (last assignment wins — fix your config).",
                        resolved, self._zones[key].value, zone_type.value, zone_type.value,
                    )
                self._zones[key] = zone_type

        self._config_loaded = True
        logger.info("ZoneResolver loaded %d zone entries from %s", len(self._zones), config_path)

    def resolve(self, path: Path) -> ZoneType:
        """Return the ZoneType for a filesystem path.

        Evaluation order:
          1. Hard-coded safe_workspace override (config.WORKSPACE)
          2. Hard-coded forbidden overrides (~/.ssh etc.)
          3. Longest-prefix match from zone_config.yaml
          4. Default: restricted

        Raises AmbiguousZoneError if two config entries match at equal depth.
        """
        resolved = path.resolve()

        # 1. Hard-coded safe_workspace
        try:
            resolved.relative_to(config.WORKSPACE.resolve())
            return ZoneType.SAFE_WORKSPACE
        except ValueError:
            pass

        # 2. Hard-coded forbidden paths
        for forbidden_root in _HARDCODED_FORBIDDEN:
            try:
                resolved.relative_to(forbidden_root.resolve())
                return ZoneType.FORBIDDEN
            except ValueError:
                pass

        # 3. Longest-prefix match from config
        best_match: Optional[ZoneType] = None
        best_depth: int = -1
        tie: bool = False

        for zone_path_str, zone_type in self._zones.items():
            zone_path = Path(zone_path_str)
            try:
                rel = resolved.relative_to(zone_path)
                depth = len(zone_path.parts)
                if depth > best_depth:
                    best_depth = depth
                    best_match = zone_type
                    tie = False
                elif depth == best_depth and zone_type != best_match:
                    tie = True
            except ValueError:
                pass

        if tie:
            raise AmbiguousZoneError(str(resolved))

        if best_match is not None:
            return best_match

        # 4. Default
        return ZoneType.RESTRICTED
