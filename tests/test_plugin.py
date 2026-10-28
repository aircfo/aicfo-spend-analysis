"""The repo installs as one Claude plugin: the engine and the skill, released together."""
from __future__ import annotations

import json
import re
from pathlib import Path

import aicfo_spend

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ai-spend-analysis"


def manifest(name: str) -> dict[str, object]:
    loaded = json.loads((ROOT / ".claude-plugin" / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def listed_plugin() -> dict[str, object]:
    listed = manifest("marketplace.json")["plugins"]
    assert isinstance(listed, list) and len(listed) == 1
    plugin = listed[0]
    assert isinstance(plugin, dict)
    return plugin


def test_the_plugin_and_the_engine_share_one_version() -> None:
    assert manifest("plugin.json")["version"] == aicfo_spend.__version__


def test_an_install_gets_the_release_of_that_version_not_the_default_branch() -> None:
    plugin = listed_plugin()

    assert plugin["name"] == manifest("plugin.json")["name"]
    assert plugin["source"] == {
        "source": "github",
        "repo": "aircfo/aicfo-spend-analysis",
        "ref": f"v{aicfo_spend.__version__}",
    }


def test_the_skill_is_named_for_the_plugin_and_its_folder() -> None:
    front_matter = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]

    name = re.search(r"^name:\s*(\S+)$", front_matter, re.MULTILINE)

    assert name is not None
    assert name.group(1) == SKILL.name == manifest("plugin.json")["name"]


def test_every_file_the_skill_lists_ships_with_it() -> None:
    files_table = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("## Files", 1)[1]

    listed = re.findall(r"^\| `([^`]+)` \|", files_table, re.MULTILINE)

    assert listed
    assert [path for path in listed if not (SKILL / path).is_file()] == []
