# AGENTS.md

## Scope
This repository is agent-first. All outputs should be machine-parseable and envelope-aware when invoking CLI tools.

## Standard workflow
1. Use shared workspace and environment for installs.
2. Run commands through `carapace`/`carapace-bws` entrypoints.
3. Keep command output in YAML HATEOAS form when chaining agent operations.

## Setup
- Install in editable mode from repo root:
  - `source /Users/openclaw/.openclaw/venv/bin/activate`
  - `python -m pip install -e .`
- Optional tests deps:
  - `python -m pip install -e '.[dev]'`

## BWS wrapper (important)
- Primary command: `carapace-bws`
- Recommended alias: `alias bws='carapace-bws'`
- Recommended env:
  - `CARAPACE_BWS_TOKEN` (or `BWS_ACCESS_TOKEN`)
  - `CARAPACE_BWS_PROJECT_ID` (or pass project UUID explicitly)
  - `CARAPACE_BWS_BINARY` when you must pin the underlying `bws` executable
- Keep behavior:
  - `list/get/set/delete` remain strict, HATEOAS responses.
  - Other commands are proxied to the real `bws` binary for compatibility.

## Core commands
- `carapace`: orchestrator (`cycle-time`, `composition-report`, `queue`, `trigger`, `gt`, etc.)
- `carapace-bws`: secrets wrapper/proxy
- `gt`: Gitea issue tools
- `carapace-redis-manager`: queue helper

## Error handling
- `carapace-bws` and `gt` emit HATEOAS envelopes with:
  - `command`, `ok`, `result`/`error`, `next_actions`
- Prefer `next_actions` for machine chaining when choosing follow-ups.
