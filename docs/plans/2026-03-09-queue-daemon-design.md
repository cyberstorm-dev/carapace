# Queue daemon integration design

**Goal:** Integrate the Redis-backed queue polling loop into the primary Carapace CLI instead of maintaining a separate service/entrypoint. Provide a daemonized mode that recalculates the ready queue every 60 seconds and publishes it to Redis while keeping the existing one-shot queue inspection behavior.

## Context
- `carapace.core.queue.run` already supports one-shot queue evaluation and optional Redis reads via `--redis-url`.
- `carapace.cli.redis_manager` implements a separate `run_manager` loop that recomputes the DAG and rewrites a Redis sorted set keyed by `carapace:queue:<repo>`.
- The ask is to expose this loop via the main CLI (e.g., `carapace queue --daemon`), defaulting to a 60-second interval, with a configurable `REDIS_URL`.

## Approach
1. Add a daemon mode flag (e.g., `--daemon`) and `--poll-interval` to the `carapace queue` command. Default poll interval to 60 seconds (configurable via `POLL_INTERVAL`). Make `--redis-url` default to `REDIS_URL` env.
2. Reuse the existing Redis queue update logic in a shared helper (move the loop from `carapace.cli.redis_manager` into `carapace.core.queue`, e.g., `run_daemon`). Keep `carapace-redis-manager` as a thin wrapper calling the shared helper for backward compatibility.
3. Preserve one-shot queue behavior; daemon mode should require a Redis URL and run the loop with logging and graceful Ctrl-C exit.
4. Update tests to target the shared helper and cover the daemon path (ensuring the loop writes the correct scores and deletes stale entries). Update defaults expectations.

## Risks / Mitigations
- **Duplicate logic divergence**: centralize loop in one helper to avoid drift.
- **Non-terminating loop in tests**: patch `time.sleep` to throw and exit after one iteration.
- **Config mismatch**: ensure both CLI args and env defaults (`REDIS_URL`, `POLL_INTERVAL`) are honored.

## Acceptance
- `carapace queue --daemon --redis-url <url>` runs a 60s polling loop that recomputes the ready queue and rewrites `carapace:queue:<repo>`.
- `REDIS_URL` is configurable (env + flag) for both daemon and one-shot Redis read modes.
- `carapace-redis-manager` continues to work via the shared helper.
- Tests updated/passing.