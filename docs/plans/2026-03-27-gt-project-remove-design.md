# gt project remove Design

## Goal
Add project card removal to `gt project` with agent-first envelopes, defaults that minimize surprise, and validation that prevents silent no-ops.

## Scope
- New subcommand: `gt project remove <issue> [--project-id <id>] [--use-default]`
- Works with existing project board scraping and web endpoints (cookie + CSRF) used by add/move.
- HATEOAS envelopes for success/error; surfaced HTTP code/reason on Gitea web errors.

## Defaults & UX
- Project selection:
  - If `--project-id` given, use it.
  - Else if `--use-default`, find the default Kanban board (Backlog/To Do/In Progress/Done) as we already do; error if not found.
  - Else fail fast: require `--project-id` or `--use-default` with a fix message.
- Missing card: error (least surprise) with next_actions pointing to `gt project cards` and `gt project list`.
- Multiple cards for same issue on a board: treat as unexpected, fail with guidance to inspect cards.

## Data flow
1) Resolve project id per rules above.
2) List cards for project (filter by issue) via existing `list_project_cards` to validate membership and locate column info.
3) If zero cards → error envelope.
4) If >1 → error envelope about duplicates.
5) Perform delete via web POST to `issues/projects/delete?issue_ids=<issue_id>` with form payload `id=<project_id>` (mirrors add/move patterns). On HTTPError, raise `GiteaAPIError` with code/reason.
6) Success envelope includes project_id, issue, and removed_from column name if known.

## Tests (TDD)
- remove without project and without --use-default → fails with message to supply project or --use-default.
- remove with --use-default but no default project found → fails with appropriate message.
- remove when card missing → error envelope ok: false, code/message present.
- remove when multiple cards found → error envelope.
- happy path mocks: cards returns one card with column; delete request succeeds → ok envelope carries project_id/issue/column.
- GiteaAPIError during delete surfaces code/reason via fail helper.

## Files to touch
- `carapace/cli/gt.py` (GiteaClient delete call, CLI wiring, validation)
- `tests/test_gt.py` (new tests for remove flows)
- Possibly add small helpers if needed for re-use.

## Open questions
- None: defaults decided above.
