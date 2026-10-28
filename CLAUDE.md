# aicfo-spend-analysis

Open-source AI spend analysis built on Ramp: the engine in `src/aicfo_spend/` and the Claude skill in `skills/ai-spend-analysis/`, released together as one plugin. The design is [docs/design.md](docs/design.md); read it before changing behavior, and update it in the same change when behavior changes. A change to the skill's steps updates its `SKILL.md` too.

## Check command

```
uv run pytest && uv run mypy
```

Run it before calling a change done. CI runs the same on Python 3.10 and 3.13.

## Rules

- **Standard library only at run time**, Python 3.10 or later. Cloud routines can't install packages. Dev tools (pytest, mypy) live in the `dev` dependency group. The one exception is the skill's optional Mac export script, which brings Playwright itself with `uv run --with playwright`.
- **One version.** `.claude-plugin/plugin.json`, `src/aicfo_spend/__init__.py`, `ENGINE_VERSION` in the skill's `scripts/run_engine.py` and the release tag in `.claude-plugin/marketplace.json` always match, and tests check it. Installs get that tag, never `main`. Releasing follows [docs/releasing.md](docs/releasing.md), which also pins the uploaded wheel's SHA-256 in `run_engine.py`.
- **Money is `Decimal`**, never `float`. Read JSON with `parse_float=Decimal`.
- **Type-hint everything.** mypy runs strict, and an explicit `Any` is not allowed: take `object` and narrow it.
- **Errors people see are plain words** that say what to fix, raised as `SettingsError` or `WorkspaceError` and printed by the CLI.
- **Tests use made-up data** and are named as sentences. Never commit a company's files, real names or keys; check every change against real names from the people list and spend exports before committing.
- **Branches and pull requests only.** The default branch is protected. Use conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- **Changes under `.github/` are CI config** and need a code owner's approval.
- **`legacy/` holds the v0.3.0 scripts.** Port from it; don't edit it. It's deleted at the cut-over.
