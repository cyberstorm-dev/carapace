"""Backfill agent-selection justification comments onto existing issues.

This tool complements :mod:`carapace.bootstrap_phase_issue` by posting
standardized justification comments to issues that already exist. Specs are
provided as YAML/JSON, reusing the same structure accepted by the bootstrapper
(one entry per issue). Required fields:

- ``number``: Issue number to comment on
- ``assignee``: Login of the chosen agent
- ``justification`` (or ``rationale``): Why this agent
- ``capability``: Capability that makes them the right fit
- ``category`` (optional): Problem category for traceability

Example spec (YAML)::

    issues:
      - number: 41
        assignee: builder
        category: process
        justification: Builder owns automation and repo guardrails for Phase 3
        capability: Python automation, Gitea API wiring

The CLI supports ``--dry-run`` to print actions without posting to Gitea.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from typing import Any, Dict, List, Optional

from carapace.bootstrap_phase_issue import (
    GiteaClient,
    build_justification_comment,
    load_specs,
)

JsonDict = Dict[str, Any]
Requester = Any


def _issue_number(issue: JsonDict) -> int:
    for key in ("number", "issue", "id"):
        if key in issue:
            return int(issue[key])
    raise ValueError("Issue spec missing 'number'")


def post_justifications(
    *,
    spec_path: str,
    repo: str,
    gitea_url: str,
    token: str,
    dry_run: bool = False,
    requester: Requester = urllib.request.urlopen,
) -> List[JsonDict]:
    if not token and not dry_run:
        raise ValueError("Gitea token is required unless --dry-run")

    specs = load_specs(spec_path)
    client = GiteaClient(repo=repo, token=token or "", base_url=gitea_url, requester=requester)

    results: List[JsonDict] = []
    for issue in specs:
        number = _issue_number(issue)
        body = build_justification_comment(issue)
        if dry_run:
            results.append({"issue": number, "body": body, "dry_run": True})
            continue
        client.post_comment(number, body)
        results.append({"issue": number, "body": body})
    return results


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill agent selection justification comments")
    parser.add_argument("--spec", required=True, help="Path to YAML/JSON issue spec")
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITEA_REPO", "openclaw/nisto-home"),
        help="Target repo (owner/name)",
    )
    parser.add_argument(
        "--gitea-url",
        default=os.environ.get("GITEA_URL", "http://100.73.228.90:3000"),
        help="Gitea base URL",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITEA_TOKEN"),
        help="Gitea token",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without posting")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        results = post_justifications(
            spec_path=args.spec,
            repo=args.repo,
            gitea_url=args.gitea_url,
            token=args.token,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for result in results:
        if result.get("dry_run"):
            print(f"[dry-run] Would post justification to issue #{result['issue']}")
        else:
            print(f"Posted justification to issue #{result['issue']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
