#!/usr/bin/env python3
"""Run the aicfo-spend engine on a company's folder.

Usage:
  python3 run_engine.py <folder> <command> [arguments...]
  python3 run_engine.py ~/Desktop/AI-Spend report 2026-10

The engine is the open-source tool in this repo, github.com/aircfo/aicfo-spend-analysis.
Installed as a plugin, or run from a checkout, the skill sits in the same repo as the
engine's source, so it runs that source: always the version the skill was written for.

A skill folder copied on its own, such as one uploaded to claude.ai, has no source
beside it. Then the first run downloads the release named below into <folder>/.engine/,
checks it against the fingerprint (SHA-256) pinned here, and keeps it; every later run
uses that file offline. A file that doesn't match is never run. Either way the engine is
plain Python, so it runs with nothing installed, the same in Cowork's shell and on a Mac.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

# Kept equal to the engine's own version by a test, so a release can't ship a skill
# that downloads an older engine than the one beside it.
ENGINE_VERSION = "0.2.0"
WHEEL_NAME = f"aicfo_spend_analysis-{ENGINE_VERSION}-py3-none-any.whl"
WHEEL_URL = f"https://github.com/aircfo/aicfo-spend-analysis/releases/download/v{ENGINE_VERSION}/{WHEEL_NAME}"
WHEEL_SHA256 = "3f8d310a1dc7eec8c459ec7449bfac8539827a31e970433ab09de017b1e0833b"
ENGINE_FOLDER = ".engine"
MIN_PYTHON = (3, 10)

# skills/ai-spend-analysis/scripts/run_engine.py -> the repo's src/. Walking up with
# .parent never fails, even when a copied skill folder sits near the top of the disk.
BUNDLED_SOURCE = Path(__file__).resolve().parent.parent.parent.parent / "src"


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        content: bytes = response.read()
        return content


def engine_path(folder: Path, fetch: Callable[[str], bytes] = download, bundled: Path = BUNDLED_SOURCE) -> Path:
    """Where to run the engine from: the source beside the skill, or else the pinned release."""
    if (bundled / "aicfo_spend" / "__init__.py").is_file():
        return bundled
    return engine_wheel(folder, fetch)


def engine_wheel(folder: Path, fetch: Callable[[str], bytes] = download) -> Path:
    """The pinned engine in the folder, downloading and checking it first if it's missing or altered."""
    wheel = folder / ENGINE_FOLDER / WHEEL_NAME
    if wheel.is_file() and _sha256(wheel.read_bytes()) == WHEEL_SHA256:
        return wheel
    content = fetch(WHEEL_URL)
    if _sha256(content) != WHEEL_SHA256:
        raise SystemExit(
            f"aicfo-spend: the download of engine {ENGINE_VERSION} doesn't match its pinned fingerprint, "
            "so it wasn't run. Try again later; if it keeps happening, report it at "
            "github.com/aircfo/aicfo-spend-analysis/issues."
        )
    wheel.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the final name, then renamed, so an interrupted download is never mistaken for the engine.
    partial = wheel.with_suffix(".partial")
    partial.write_bytes(content)
    os.replace(partial, wheel)
    return wheel


def main(argv: Sequence[str], fetch: Callable[[str], bytes] = download, bundled: Path = BUNDLED_SOURCE) -> int:
    if sys.version_info < MIN_PYTHON:
        found = ".".join(str(part) for part in sys.version_info[:3])
        raise SystemExit(f"aicfo-spend: needs Python 3.10 or later, and this is {found}")
    if len(argv) < 2:
        raise SystemExit(__doc__)
    folder, command, rest = Path(argv[0]).expanduser(), argv[1], list(argv[2:])
    engine = engine_path(folder, fetch, bundled)
    environment = {**os.environ, "PYTHONPATH": str(engine)}
    return subprocess.call([sys.executable, "-m", "aicfo_spend", command, str(folder), *rest], env=environment)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
