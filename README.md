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

## Carapace BWS (agent-first)

`carapace-bws` is the canonical agent entrypoint for secret automation.
It wraps the real `bws` binary, returns YAML HATEOAS envelopes, and proxies unknown
commands through to the underlying `bws` executable when needed.

For automatic agent setup, use:

- [`examples/agent-bootstrap.md`](/Users/allenday/src/carapace/examples/agent-bootstrap.md)

Recommended setup for low-friction agent sessions:

```bash
# one-time agent bootstrap (shared across shell/session)
export CARAPACE_BWS_TOKEN="${BWS_TOKEN}"
export CARAPACE_BWS_PROJECT_ID="<project-uuid>"
export CARAPACE_BWS_BINARY="$(/usr/bin/which bws)"
alias bws='carapace-bws'
```

Use these patterns (all are HATEOAS-ready):

```bash
carapace-bws list
carapace-bws get <key>
carapace-bws set <key> <value> --note "<reason>"
```

You can still run native `bws` operations through the proxy:

```bash
bws secret list <project-uuid>
```

If you need to target a custom `bws` binary (for example, if PATH is noisy), set:

```bash
export CARAPACE_BWS_BINARY="/path/to/real-bws"
```

## Tests
From the repo root (inside the shared venv):

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```
