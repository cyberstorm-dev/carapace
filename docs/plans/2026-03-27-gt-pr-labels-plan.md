# gt PR labels Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add PR label management and label-based filtering to `gt pr`, mirroring existing issue label semantics.

**Architecture:** Reuse issue label endpoints because Gitea treats PRs as issues; add CLI wiring for PR label add/rm and allow `pr list` to filter by labels using the issues search (type=pulls). Maintain HATEOAS envelopes and centralized error handling.

**Tech Stack:** Python, urllib, pytest; existing gt envelope helpers.

### Task 1: Add failing tests for PR label flows
**Files:**
- Modify: `tests/test_gt.py`

Steps:
1. Add tests for:
   - `gt pr label add <pr> <label>` calls `add_label` with pr number.
   - `gt pr label rm <pr> <label>` calls `remove_label`.
   - Errors from GiteaAPIError surface code/reason for pr label add.
   - `gt pr list --labels l1,l2` passes labels through and returns ok envelope.
2. Run `PYTHONPATH=. pytest -q tests/test_gt.py` and confirm new tests fail.

### Task 2: Extend client/pr list for labels
**Files:**
- Modify: `carapace/cli/gt.py`

Steps:
1. Update `list_pulls` to accept `labels` and include in query (via issues search `pulls?labels=...` or equivalent supported params).
2. Ensure downstream code consumes `labels` argument.

### Task 3: Add PR label subcommands
**Files:**
- Modify: `carapace/cli/gt.py`

Steps:
1. Add `pr label` parser with subactions `add` and `rm` (args: pull, label_id).
2. Route to `add_label` / `remove_label` (reusing issue label methods).
3. Envelopes with contextual next_actions (e.g., pr list with labels).

### Task 4: Wire pr list labels filter
**Files:**
- Modify: `carapace/cli/gt.py`

Steps:
1. Add `--labels` to `pr list` parser.
2. Pass labels into `list_pulls` and include echo in result for clarity.

### Task 5: Make tests pass
**Files:**
- Modify as needed.

Steps:
1. Run `PYTHONPATH=. pytest -q tests/test_gt.py` until green.

### Task 6: Final verification & PR
**Files:**
- All touched files.

Steps:
1. Run full gt tests: `PYTHONPATH=. pytest -q tests/test_gt.py`.
2. `git status` clean of unintended files.
3. Summarize changes; prepare PR body (summary + test plan).

