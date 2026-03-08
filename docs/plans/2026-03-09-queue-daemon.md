# Queue Daemon Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a daemon/polling mode to the `carapace queue` CLI (default 60s interval, configurable `REDIS_URL`) that publishes the ready queue to Redis, reusing existing logic and keeping the `carapace-redis-manager` entrypoint working.

**Architecture:** Centralize the Redis publishing loop in `carapace.core.queue` (e.g., `run_daemon`), invoked by both `carapace queue --daemon` and the existing `carapace-redis-manager` wrapper. The loop recomputes the ready queue, rewrites the Redis sorted set `carapace:queue:<repo>`, and sleeps for a configurable interval.

**Tech Stack:** Python 3.9+, argparse CLI, redis-py, pytest.

---

### Task 1: Add CLI flags for daemon mode

**Files:**
- Modify: `carapace/cli/main.py`
- Modify: `carapace/core/queue.py`

**Steps:**
1. Add `--daemon` flag and `--poll-interval` (default from `POLL_INTERVAL` env or 60) to the queue subparser. Set `--redis-url` default from `REDIS_URL` env.
2. In `queue.run`, branch early for daemon mode: validate `redis_url` presence; call the daemon helper; return appropriate exit code.

### Task 2: Centralize the Redis polling loop

**Files:**
- Modify: `carapace/core/queue.py`
- Modify: `carapace/cli/redis_manager.py`

**Steps:**
1. Move/implement the polling loop as a shared helper (e.g., `run_daemon(...)`) that computes ready issues via `Scheduler.compute_ready_queue`, rewrites the Redis zset, logs, and sleeps.
2. Update `carapace-redis-manager` to delegate to the shared helper for backward compatibility.

### Task 3: Update tests for daemon mode

**Files:**
- Modify: `tests/test_redis_manager.py`
- Add/Modify: `tests/test_queue_daemon.py` (or extend existing) to cover the daemon helper

**Steps:**
1. Adjust tests to patch `time.sleep` to exit after one iteration; assert Redis operations and scheduler calls.
2. Cover the new helper location/behavior and ensure defaults (e.g., 60s, env-driven REDIS_URL) are accessible if needed.

### Task 4: Docs and default verification

**Files:**
- Modify: `README.md`

**Steps:**
1. Document `carapace queue --daemon` usage, interval default, and `REDIS_URL` configurability.
2. Note backward-compatible `carapace-redis-manager` wrapper if applicable.

### Task 5: Validate

**Files:**
- N/A (commands)

**Steps:**
1. Run `python -m pytest -q` to ensure tests pass.
2. Smoke-check CLI help for new flags (`carapace queue --help`).

### Task 6: Commit and PR

**Files:**
- N/A (commands)

**Steps:**
1. `git status` to verify changes; commit with message like `feat: add queue daemon polling mode`.
2. Push branch and open PR via `gh pr create`.
3. Comment on Gitea issue #281 with PR link.
