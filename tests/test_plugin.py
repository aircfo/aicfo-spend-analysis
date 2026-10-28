"""The repo installs as one Claude plugin: the engine and the skill, released together."""
from __future__ import annotations

import json
from pathlib import Path

import aicfo_spend

ROOT = Path(__file__).resolve().parents[1]


def manifest(name: str) -> dict[str, object]:
    loaded = json.loads((ROOT / ".claude-plugin" / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_plugin_and_the_engine_share_one_version() -> None:
    assert manifest("plugin.json")["version"] == aicfo_spend.__version__


def test_the_marketplace_lists_this_repo_as_the_plugin() -> None:
    listed = manifest("marketplace.json")["plugins"]
    assert isinstance(listed, list) and len(listed) == 1
    plugin = listed[0]
    assert isinstance(plugin, dict)

    assert plugin["name"] == manifest("plugin.json")["name"]
    assert plugin["source"] == "./"


def test_the_plugin_carries_the_skill() -> None:
    assert (ROOT / "skills" / "ai-spend-analysis" / "SKILL.md").is_file()
