# Carapace CLI

Agent-first CLI toolkit emitting YAML HATEOAS envelopes for agent workflows.

## Upstream
- Repo: https://github.com/cyberstorm-dev/carapace (branch: `main`)
- Local clone (for agents): `/Users/openclaw/.openclaw/agents/cloudops/carapace`

## Install (shared venv)
Use the shared OpenClaw virtualenv so all agents pick up the same editable install:

```bash
source /Users/openclaw/.openclaw/venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e /Users/openclaw/.openclaw/agents/cloudops/carapace
```

## Commands
Primary entrypoints (after the install above):

- `carapace` (dispatcher: cycle-time, cycle-time-report, composition-report, queue, trigger, gatus-check)
- `phase-validator` (issue graph validator)
- `phase-issue-bootstrap` (Phase 3 issue bootstrapper)
- `carapace-bws` (BWS helper)
- `carapace-redis-manager` (priority queue manager)
- `gt` (Gitea helper)
- `ci-metrics`, `pipeline-metrics`, `task-timeline-metrics`, `reviewer-metrics`

## Tests
From the repo root (inside the shared venv):

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```
