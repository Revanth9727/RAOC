"""Tests for raoc.substrate.zone_resolver.ZoneResolver."""

import logging
from pathlib import Path

import pytest

from raoc import config
from raoc.models.policy import ZoneType
from raoc.substrate.exceptions import AmbiguousZoneError
from raoc.substrate.zone_resolver import ZoneResolver


@pytest.fixture()
def config_file(tmp_path):
    """Write a minimal zone_config.yaml to a temp path and return that path."""
    content = """
safe_workspace:
  - ~/raoc_workspace

read_only:
  - ~/Documents/Reference
  - ~/Downloads

restricted:
  - ~/Desktop
  - ~/Documents

forbidden:
  - ~/.ssh
  - ~/Library/Keychains
  - ~/.aws
  - ~/.config
"""
    p = tmp_path / 'zone_config.yaml'
    p.write_text(content)
    return p


@pytest.fixture()
def resolver(config_file):
    return ZoneResolver(config_file)


def test_workspace_always_safe(resolver, tmp_path, monkeypatch):
    """Any path under config.WORKSPACE resolves to safe_workspace regardless of config."""
    fake_workspace = tmp_path / 'raoc_workspace'
    fake_workspace.mkdir()
    monkeypatch.setattr(config, 'WORKSPACE', fake_workspace)
    target = fake_workspace / 'scripts' / 'foo.py'
    assert resolver.resolve(target) == ZoneType.SAFE_WORKSPACE


def test_ssh_always_forbidden(resolver):
    """~/.ssh is hard-coded forbidden and cannot be overridden by config."""
    ssh_path = Path.home() / '.ssh' / 'id_rsa'
    assert resolver.resolve(ssh_path) == ZoneType.FORBIDDEN


def test_all_hardcoded_forbidden_paths(resolver):
    """~/Library/Keychains, ~/.aws, ~/.config are also hard-coded forbidden."""
    home = Path.home()
    assert resolver.resolve(home / 'Library' / 'Keychains' / 'login.keychain') == ZoneType.FORBIDDEN
    assert resolver.resolve(home / '.aws' / 'credentials') == ZoneType.FORBIDDEN
    assert resolver.resolve(home / '.config' / 'some_app' / 'config') == ZoneType.FORBIDDEN


def test_most_specific_match_wins(resolver):
    """~/Documents/Reference/report.pdf → read_only, not restricted (~/Documents)."""
    path = Path.home() / 'Documents' / 'Reference' / 'report.pdf'
    assert resolver.resolve(path) == ZoneType.READ_ONLY


def test_missing_config_logs_warning_and_uses_restricted(tmp_path, caplog):
    """Missing zone_config.yaml logs a warning and falls back to restricted for unknown paths."""
    missing = tmp_path / 'nonexistent.yaml'
    with caplog.at_level(logging.WARNING):
        r = ZoneResolver(missing)
    assert any('zone_config' in record.message.lower() or 'missing' in record.message.lower()
               for record in caplog.records)
    # An arbitrary path with no hard-coded override should be restricted
    assert r.resolve(Path.home() / 'Desktop' / 'foo.txt') == ZoneType.RESTRICTED
