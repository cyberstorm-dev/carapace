# Carapace BWS Bootstrap (one screen)

## Fastest path

```bash
# set required values first
export CARAPACE_BWS_TOKEN="<your-bws-token>"
export CARAPACE_BWS_PROJECT_ID="<project-uuid>"

# optional overrides if needed
# export CARAPACE_REPO_ROOT=/path/to/carapace
# export CARAPACE_VENV=/path/to/venv/bin/activate
# export CARAPACE_BWS_BINARY=/path/to/real/bws

cd /path/to/carapace
source examples/agent-bootstrap.sh
```

## Validate it works

```bash
source examples/agent-bootstrap.validate.sh
```

Expected output:
- `PASS: carapace-bws --help`
- `PASS: carapace-bws list`
- `PASS: carapace-bws secret list <project_uuid>`
- `Bootstrap validation completed.`

## What this gives you

- `bws` alias to `carapace-bws`
- HATEOAS-friendly output for wrapped commands (`list/get/set/delete`)
- passthrough support for other `bws` commands via the real binary
