"""Carapace command dispatcher producing YAML HATEOAS envelopes."""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict

from . import composition_report, cycle_time, cycle_time_report, queue
from .hateoas import dump_yaml, envelope


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Carapace CLI (YAML HATEOAS)")
    sub = parser.add_subparsers(dest="command")

    queue_parser = sub.add_parser("queue", help="Get the scheduler ready queue")
    queue_parser.add_argument("--milestone", required=True, type=int)
    queue_parser.add_argument("--assignee", default=None)
    queue_parser.add_argument("--claim", action="store_true", help="Claim the top issue (marks in-progress)")
    queue_parser.add_argument("--repo", default=None)
    queue_parser.add_argument("--gitea-url", default=None)
    queue_parser.add_argument("--token", default=None)

    cycle_parser = sub.add_parser("cycle-time", help="Record cycle-time entry")
    cycle_parser.add_argument("--issue", type=int, required=True)
    cycle_parser.add_argument("--started-at", required=True)
    cycle_parser.add_argument("--decomposed-at", required=True)
    cycle_parser.add_argument("--finished-at", required=True)
    cycle_parser.add_argument("--delegation-outperformed", required=True, type=cycle_time._bool_arg)
    cycle_parser.add_argument("--notes")
    cycle_parser.add_argument("--executor")
    cycle_parser.add_argument("--comment", action="store_true")
    cycle_parser.add_argument("--repo", default=None)
    cycle_parser.add_argument("--gitea-url", default=None)
    cycle_parser.add_argument("--token", default=None)

    report_parser = sub.add_parser("cycle-time-report", help="Auto-generate cycle-time report from Gitea")
    report_parser.add_argument("--issues", required=True, help="Comma-separated issue numbers")
    report_parser.add_argument("--repo", default=None)
    report_parser.add_argument("--gitea-url", default=None)
    report_parser.add_argument("--token", default=None)
    report_parser.add_argument("--format", choices=["markdown", "json", "yaml"], default="yaml", dest="output_format")
    report_parser.add_argument("--comment-on", type=int, default=None)

    comp_parser = sub.add_parser("composition-report", help="Cross-agent composition reporter")
    comp_parser.add_argument("--repo", default="openclaw/nisto-home")
    comp_parser.add_argument("--gitea-url", default=None)
    comp_parser.add_argument("--token", default=None)
    comp_parser.add_argument("--milestone", default=None)
    comp_parser.add_argument("--format", choices=["markdown", "json", "yaml"], default="markdown")
    return parser


def _root_payload() -> Dict[str, Any]:
    return envelope(
        command="carapace",
        ok=True,
        result={
            "description": "Agent-first CLI toolkit (YAML envelopes)",
            "commands": [
                {"name": "cycle-time", "description": "Record cycle-time data and optional comment"},
                {"name": "cycle-time-report", "description": "Auto-generate cycle-time report from Gitea API"},
                {"name": "pr-issue-ref", "description": "Validate PR body links to an issue"},
                {"name": "composition-report", "description": "Cross-agent output composition reporter"},
            ],
        },
        next_actions=[
            {"command": "carapace cycle-time --help", "description": "View cycle-time options"},
            {"command": "carapace pr-issue-ref", "description": "Run PR issue reference check"},
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        print(dump_yaml(_root_payload()))
        return 0

    if args.command == "queue":
        return queue.run(args)

    if args.command == "cycle-time":
        command_str = "carapace " + " ".join(sys.argv[1:] if argv is None else argv)
        payload, code = cycle_time.run(args, command=command_str)
        print(dump_yaml(payload))
        return code

    if args.command == "cycle-time-report":
        cli_args = ["--issues", args.issues]
        if args.repo:
            cli_args.extend(["--repo", args.repo])
        if args.gitea_url:
            cli_args.extend(["--gitea-url", args.gitea_url])
        if args.token:
            cli_args.extend(["--token", args.token])
        cli_args.extend(["--format", args.output_format])
        if args.comment_on:
            cli_args.extend(["--comment-on", str(args.comment_on)])
        return cycle_time_report.run(cli_args)

    if args.command == "composition-report":
        return composition_report.main([
            "--repo", args.repo,
            *(["--gitea-url", args.gitea_url] if args.gitea_url else []),
            *(["--token", args.token] if args.token else []),
            *(["--milestone", args.milestone] if args.milestone else []),
            "--format", args.format,
        ])

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    sys.exit(main())
