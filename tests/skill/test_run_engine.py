"""The engine launcher: the source beside the skill, or else the pinned release, downloaded once."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import urllib.error
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import aicfo_spend
import run_engine

# A stand-in engine: it records the command line it was given, so a test can see it ran.
FAKE_MAIN = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "Path(sys.argv[2], 'ran.json').write_text(json.dumps(sys.argv[1:]))\n"
)


def fake_wheel() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as wheel:
        wheel.writestr("aicfo_spend/__init__.py", "")
        wheel.writestr("aicfo_spend/__main__.py", FAKE_MAIN)
    return buffer.getvalue()


class Downloads:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.content


class CopiedSkillTest(unittest.TestCase):
    """A skill folder copied on its own, with no engine source beside it."""

    def setUp(self) -> None:
        self.wheel = fake_wheel()
        pinned = mock.patch.object(run_engine, "WHEEL_SHA256", hashlib.sha256(self.wheel).hexdigest())
        pinned.start()
        self.addCleanup(pinned.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "acme"
        self.folder.mkdir()
        self.no_source = Path(temporary.name) / "no-source"

    def launch(self, argv: list[str], downloads: Downloads) -> int:
        return run_engine.main(argv, downloads, bundled=self.no_source)

    def test_runs_the_command_with_the_folder_after_it(self) -> None:
        status = self.launch([str(self.folder), "report", "2026-09"], Downloads(self.wheel))

        self.assertEqual(status, 0)
        ran = json.loads((self.folder / "ran.json").read_text())
        self.assertEqual(ran, ["report", str(self.folder), "2026-09"])

    def test_downloads_the_engine_once_then_runs_offline(self) -> None:
        downloads = Downloads(self.wheel)

        self.launch([str(self.folder), "settings"], downloads)
        self.launch([str(self.folder), "settings"], downloads)

        self.assertEqual(downloads.urls, [run_engine.WHEEL_URL])

    def test_refuses_a_download_that_does_not_match_the_fingerprint(self) -> None:
        with self.assertRaises(SystemExit) as refused:
            self.launch([str(self.folder), "settings"], Downloads(b"not the engine"))

        self.assertIn("doesn't match its pinned fingerprint", str(refused.exception))
        self.assertFalse((self.folder / run_engine.ENGINE_FOLDER / run_engine.WHEEL_NAME).exists())
        self.assertFalse((self.folder / "ran.json").exists())

    def test_replaces_an_engine_file_that_was_altered(self) -> None:
        downloads = Downloads(self.wheel)
        self.launch([str(self.folder), "settings"], downloads)
        (self.folder / run_engine.ENGINE_FOLDER / run_engine.WHEEL_NAME).write_bytes(b"altered")

        self.launch([str(self.folder), "settings"], downloads)

        self.assertEqual(len(downloads.urls), 2)
        self.assertEqual((self.folder / run_engine.ENGINE_FOLDER / run_engine.WHEEL_NAME).read_bytes(), self.wheel)

    def test_explains_itself_when_given_too_little(self) -> None:
        with self.assertRaises(SystemExit) as refused:
            self.launch([str(self.folder)], Downloads(self.wheel))

        self.assertIn("Usage:", str(refused.exception))

    def test_explains_a_failed_download_in_plain_words(self) -> None:
        def offline(url: str) -> bytes:
            raise urllib.error.URLError("no route to host")

        with self.assertRaises(SystemExit) as refused:
            run_engine.main([str(self.folder), "settings"], offline, bundled=self.no_source)

        self.assertIn("couldn't download engine", str(refused.exception))
        self.assertIn("no route to host", str(refused.exception))



class BundledSourceTest(unittest.TestCase):
    """A plugin install or a checkout, where the engine's source sits beside the skill."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "acme"
        self.folder.mkdir()
        self.source = Path(temporary.name) / "src"
        (self.source / "aicfo_spend").mkdir(parents=True)
        (self.source / "aicfo_spend" / "__init__.py").write_text("")
        (self.source / "aicfo_spend" / "__main__.py").write_text(FAKE_MAIN)
        (Path(temporary.name) / ".claude-plugin").mkdir()
        (Path(temporary.name) / ".claude-plugin" / "plugin.json").write_text("{}")

    def test_runs_the_source_beside_the_skill_without_downloading(self) -> None:
        downloads = Downloads(b"never fetched")

        status = run_engine.main([str(self.folder), "report", "2026-09"], downloads, bundled=self.source)

        self.assertEqual(status, 0)
        self.assertEqual(json.loads((self.folder / "ran.json").read_text()), ["report", str(self.folder), "2026-09"])
        self.assertEqual(downloads.urls, [])
        self.assertFalse((self.folder / run_engine.ENGINE_FOLDER).exists())

    def test_a_stray_aicfo_spend_folder_where_it_runs_never_replaces_the_engine(self) -> None:
        decoy = self.folder / "aicfo_spend"
        decoy.mkdir()
        (decoy / "__init__.py").write_text("")
        (decoy / "__main__.py").write_text("from pathlib import Path\nPath('decoy-ran').write_text('yes')\n")
        here = os.getcwd()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, here)

        run_engine.main([str(self.folder), "settings"], Downloads(b"never fetched"), bundled=self.source)

        self.assertTrue((self.folder / "ran.json").exists())
        self.assertFalse((self.folder / "decoy-ran").exists())

    def test_engine_source_without_the_plugin_beside_it_is_not_trusted(self) -> None:
        (self.source.parent / ".claude-plugin" / "plugin.json").unlink()
        downloads = Downloads(b"not the engine")

        with self.assertRaises(SystemExit):
            run_engine.main([str(self.folder), "settings"], downloads, bundled=self.source)

        self.assertEqual(downloads.urls, [run_engine.WHEEL_URL])
        self.assertFalse((self.folder / "ran.json").exists())

    def test_in_this_repo_the_skill_finds_the_engine_source(self) -> None:
        self.assertTrue((run_engine.BUNDLED_SOURCE / "aicfo_spend" / "__init__.py").is_file())

    def test_the_release_it_falls_back_to_is_the_engine_beside_it(self) -> None:
        self.assertEqual(run_engine.ENGINE_VERSION, aicfo_spend.__version__)
