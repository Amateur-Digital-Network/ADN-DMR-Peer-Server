# ADN DMR Peer Server - package version
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# python-semantic-release bumps ``pyproject.toml``; runtime reads checkout or metadata.

"""Version helpers for CLI, HELLO, and deploy scripts."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


def repo_root() -> Path:
    """Repository root (parent of ``src/``)."""
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _version_from_pyproject() -> str:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    data = tomllib.loads((repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    ver = data.get("project", {}).get("version")
    if isinstance(ver, str) and ver.strip():
        return ver.strip()
    raise ValueError("project.version missing in pyproject.toml")


@lru_cache(maxsize=1)
def read_version() -> str:
    """Human-readable version from ``pyproject.toml`` in checkout, else package metadata."""
    pyproject_path = repo_root() / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            return _version_from_pyproject()
        except ValueError:
            pass
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("adn-server")
    except PackageNotFoundError as e:
        raise ValueError(
            f"adn-server not installed and no pyproject.toml version: {pyproject_path}"
        ) from e


def read_version_pep440() -> str:
    """Normalize ``2.0.0-rc.6`` → ``2.0.0rc6`` for importlib.metadata comparisons."""
    return re.sub(r"-(?=rc|a|b|dev)", "", read_version())


__version__ = read_version()
# Sent on every download and panel report, so the file servers can tell versions apart.
USER_AGENT = f"adn-server/{__version__}"
