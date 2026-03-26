# gt project remove Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `gt project remove` to delete issue cards from project boards with agent-first envelopes, sensible defaults, and explicit errors for missing/duplicate cards.

**Architecture:** Reuse existing Gitea web scraping/client patterns. Add a delete helper that posts to the project board delete endpoint. Validate membership via `list_project_cards`. Wire CLI parser and responses through the existing envelope helpers. Tests in TDD style.

**Tech Stack:** Python, urllib, pytest, yaml envelopes already in `carapace/cli/gt.py`.

### Task 1: Add failing tests for remove flows
**Files:**
- Modify: `tests/test_gt.py`

Steps:
1. Add tests covering:
   - no project id and no `--use-default` → error envelope, message mentioning project id/--use-default.
   - `--use-default` but default project missing → error envelope.
   - missing card in project → error envelope with message/next_actions.
   - multiple cards found → error envelope.
   - success path → ok envelope with project_id/issue/removed_from.
   - GiteaAPIError during delete surfaces code/reason.
2. Run `PYTHONPATH=. pytest -q tests/test_gt.py` and confirm new tests fail.

### Task 2: Implement project card removal in client
**Files:**
- Modify: `carapace/cli/gt.py`

Steps:
1. Add `remove_issue_from_project(self, project_id: int, issue_index: int)` to `GiteaClient` using `_web_request` POST to `issues/projects/delete?issue_ids=<issue_id>` with payload `id=project_id`, raise `GiteaAPIError` on HTTPError.
2. Reuse `parse_issue_ref`/`IssueRef` as needed to get internal id if consistent with add; otherwise mirror add’s lookup logic for issue id.

### Task 3: Wire CLI subcommand
**Files:**
- Modify: `carapace/cli/gt.py`

Steps:
1. Add parser `project remove` with args: `project_id` (optional int), `issue` (int), flag `--use-default` (store_true).
2. In project action handling:
   - Resolve project id: prefer explicit; else if `--use-default` call existing `find_default_kanban_project`; else fail via `fail` helper with fix/next_actions.
   - Call `list_project_cards` filtered by issue to validate membership; branch on 0, >1, =1.
   - Perform delete via new client helper; build envelope with removed_from column name if available.
   - Provide contextual `next_actions` (cards/columns/list).

### Task 4: Make tests pass
**Files:**
- Modify: `carapace/cli/gt.py`, maybe adjust helpers.

Steps:
1. Run `PYTHONPATH=. pytest -q tests/test_gt.py` and iterate until green.

### Task 5: Final verification and PR prep
**Files:**
- `carapace/cli/gt.py`, `tests/test_gt.py`, plan/doc files.

Steps:
1. Run full test suite for gt: `PYTHONPATH=. pytest -q tests/test_gt.py`.
2. `git status` to ensure only intended files changed.
3. Summarize changes.
4. Prepare PR body with summary + test plan.

