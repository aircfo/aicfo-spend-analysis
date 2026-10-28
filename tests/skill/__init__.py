"""Tests for the Claude skill's scripts, which run as plain files rather than as a package."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "ai-spend-analysis" / "scripts"
sys.path.insert(0, str(SCRIPTS))
