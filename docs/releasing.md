# Releasing

A release is one version of the engine and the skill together. Plugin installs get the release tag the marketplace file names, never `main`, so nothing reaches users until these steps finish. Copies of the skill folder on their own download the release's wheel and check it against the fingerprint pinned in the launcher, so the fingerprint must be the uploaded file's.

1. **Branch.** `chore/release-X.Y.Z` from `main`.
2. **Set the version in all four places.** Tests fail until they match:
   - `src/aicfo_spend/__init__.py`: `__version__ = "X.Y.Z"`
   - `.claude-plugin/plugin.json`: `"version": "X.Y.Z"`
   - `.claude-plugin/marketplace.json`: the plugin's `"ref": "vX.Y.Z"`
   - `skills/ai-spend-analysis/scripts/run_engine.py`: `ENGINE_VERSION = "X.Y.Z"`
3. **Finish the changelog.** Rename `[Unreleased]` to `[X.Y.Z] — <date>`. Make any other doc change now: the wheel carries `README.md`, so editing it later changes the fingerprint.
4. **Build the wheel and pin its fingerprint.**
   ```
   uv build --wheel
   shasum -a 256 dist/aicfo_spend_analysis-X.Y.Z-py3-none-any.whl
   ```
   Put that hash in `WHEEL_SHA256` in `run_engine.py`. The launcher isn't in the wheel, so this edit doesn't change the hash. `pyproject.toml` pins hatchling exactly; a different hatchling writes a different file. Keep this exact file for step 6.
5. **Check and merge.** `uv run pytest && uv run mypy`, then a pull request and its review.
6. **Tag and publish right after the merge.** Until the tag exists, a new install fails, because the marketplace file already names it.
   ```
   git tag vX.Y.Z <merge commit> && git push origin vX.Y.Z
   gh release create vX.Y.Z dist/aicfo_spend_analysis-X.Y.Z-py3-none-any.whl --title vX.Y.Z --notes "<this version's changelog>"
   ```
7. **Confirm what users get.**
   - The uploaded file's fingerprint: `gh release download vX.Y.Z -p '*.whl' -O - | shasum -a 256` prints the hash in `WHEEL_SHA256`.
   - A copied skill folder: copy `skills/ai-spend-analysis/` to an empty scratch folder and run `python3 <it>/scripts/run_engine.py <scratch workspace> settings`. It downloads the wheel, checks it and runs.
   - A plugin install: `/plugin marketplace update aicfo-spend-analysis`, then install, then ask Claude "set up AI spend tracking" in a scratch folder.

To try unreleased work, load a checkout for one session with `claude --plugin-dir <checkout>`.
