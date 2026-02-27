"""Cycle-time helper with YAML HATEOAS envelope output."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from .hateoas import dump_yaml, envelope


@dataclass
class CycleTimeEntry:
    issue: int
    started_at: datetime
    decomposed_at: datetime
    finished_at: datetime
    delegation_outperformed: bool
    notes: Optional[str] = None
    executor: Optional[str] = None

    @property
    def durations(self) -> Dict[str, int]:
        return {
            "start_to_decompose_minutes": _minutes_between(self.started_at, self.decomposed_at),
            "decompose_to_finish_minutes": _minutes_between(self.decomposed_at, self.finished_at),
            "start_to_finish_minutes": _minutes_between(self.started_at, self.finished_at),
        }


def _parse_timestamp(raw: Optional[str], field: str) -> datetime:
    if not raw:
        raise ValueError(f"{field} is required")

    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:  # pragma: no cover - guarded by explicit ValueErrors
        raise ValueError(f"{field} must be ISO 8601 (got {raw!r})") from exc

    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")

    return parsed.astimezone(timezone.utc)


def _minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def _format_timestamp(dt: datetime) -> str:
    as_utc = dt.astimezone(timezone.utc)
    return as_utc.strftime("%Y-%m-%d %H:%MZ")


def build_entry(
    *,
    issue: int,
    started_at: Optional[str],
    decomposed_at: Optional[str],
    finished_at: Optional[str],
    delegation_outperformed: bool,
    notes: Optional[str] = None,
    executor: Optional[str] = None,
) -> CycleTimeEntry:
    start_ts = _parse_timestamp(started_at, "started_at")
    decompose_ts = _parse_timestamp(decomposed_at, "decomposed_at")
    finish_ts = _parse_timestamp(finished_at, "finished_at")

    if start_ts > decompose_ts:
        raise ValueError("started_at must be <= decomposed_at")
    if decompose_ts > finish_ts:
        raise ValueError("decomposed_at must be <= finished_at")

    return CycleTimeEntry(
        issue=issue,
        started_at=start_ts,
        decomposed_at=decompose_ts,
        finished_at=finish_ts,
        delegation_outperformed=delegation_outperformed,
        notes=notes,
        executor=executor,
    )


def to_json(entry: CycleTimeEntry) -> Dict[str, Any]:
    payload = {
        "issue": entry.issue,
        "started_at": entry.started_at.isoformat().replace("+00:00", "Z"),
        "decomposed_at": entry.decomposed_at.isoformat().replace("+00:00", "Z"),
        "finished_at": entry.finished_at.isoformat().replace("+00:00", "Z"),
        "durations": entry.durations,
        "delegation_outperformed": entry.delegation_outperformed,
    }
    if entry.notes:
        payload["notes"] = entry.notes
    if entry.executor:
        payload["executor"] = entry.executor
    return payload


def format_markdown(entry: CycleTimeEntry) -> str:
    header = (
        "Issue | Started | Decomposed | Finished | Start→Decompose | "
        "Decompose→Finish | Total | Delegation Outperformed | Executor | Notes"
    )
    separator = "|".join(["---"] * 10)
    row = " | ".join(
        [
            f"#{entry.issue}",
            _format_timestamp(entry.started_at),
            _format_timestamp(entry.decomposed_at),
            _format_timestamp(entry.finished_at),
            f"{entry.durations['start_to_decompose_minutes']}m",
            f"{entry.durations['decompose_to_finish_minutes']}m",
            f"{entry.durations['start_to_finish_minutes']}m",
            str(entry.delegation_outperformed).lower(),
            entry.executor or "",
            entry.notes or "",
        ]
    )

    return "\n".join([
        "Cycle-Time Entry",
        header,
        separator,
        row,
    ])


def _comment_body(entry: CycleTimeEntry) -> str:
    json_blob = json.dumps(to_json(entry), indent=2)
    return f"{format_markdown(entry)}\n\n```json\n{json_blob}\n```"


def post_comment(
    entry: CycleTimeEntry,
    *,
    repo: str,
    token: str,
    base_url: str,
    requester: Optional[Callable[[urllib.request.Request], Any]] = None,
) -> None:
    requester = requester or urllib.request.urlopen
    url = f"{base_url.rstrip('/')}/api/v1/repos/{repo}/issues/{entry.issue}/comments"
    payload = json.dumps({"body": _comment_body(entry)}).encode()

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"token {token}",
        },
        method="POST",
    )

    with requester(request) as response:
        response.read()


def _bool_arg(value: str) -> bool:
    truthy = {"true", "t", "1", "yes", "y"}
    falsy = {"false", "f", "0", "no", "n"}
    lowered = value.lower()
    if lowered in truthy:
        return True
    if lowered in falsy:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cycle-time helper")
    parser.add_argument("--issue", type=int, required=True, help="Issue number")
    parser.add_argument("--started-at", required=True, help="ISO timestamp (UTC)")
    parser.add_argument("--decomposed-at", required=True, help="ISO timestamp (UTC)")
    parser.add_argument("--finished-at", required=True, help="ISO timestamp (UTC)")
    parser.add_argument(
        "--delegation-outperformed",
        required=True,
        type=_bool_arg,
        help="Whether delegation beat direct execution (true/false)",
    )
    parser.add_argument("--notes", help="Optional notes")
    parser.add_argument("--executor", help="Who executed the work")
    parser.add_argument(
        "--comment",
        action="store_true",
        help="Post the entry as an issue comment",
    )
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
        help="Gitea token for commenting",
    )
    return parser


def run(args: argparse.Namespace, *, command: str) -> Tuple[Dict[str, Any], int]:
    try:
        entry = build_entry(
            issue=args.issue,
            started_at=args.started_at,
            decomposed_at=args.decomposed_at,
            finished_at=args.finished_at,
            delegation_outperformed=args.delegation_outperformed,
            notes=args.notes,
            executor=args.executor,
        )
    except ValueError as exc:
        payload = envelope(
            command=command,
            ok=False,
            error={"message": str(exc), "code": "INVALID_INPUT"},
            fix="Provide ISO-8601 timestamps with timezone; ensure ordering is start <= decomposed <= finished",
            next_actions=[{"command": f"{command} --help", "description": "See required flags"}],
        )
        return payload, 1

    if args.comment and not args.token:
        payload = envelope(
            command=command,
            ok=False,
            error={"message": "--comment requires --token", "code": "MISSING_TOKEN"},
            fix="Pass --token or set GITEA_TOKEN to authorize comment posting",
            next_actions=[{"command": f"{command} --token <token> --comment", "description": "Retry with credentials"}],
        )
        return payload, 1

    if args.comment:
        post_comment(entry, repo=args.repo, token=args.token, base_url=args.gitea_url)

    result = {
        "entry": to_json(entry),
        "markdown": format_markdown(entry),
    }
    if args.comment:
        result["comment_posted"] = True

    payload = envelope(
        command=command,
        ok=True,
        result=result,
        next_actions=[
            {"command": f"{command} --issue {entry.issue} --started-at ...", "description": "Record another entry"},
            {"command": "carapace", "description": "List available carapace commands"},
        ],
    )
    return payload, 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command_str = "carapace-cycle-time " + " ".join(sys.argv[1:] if argv is None else argv)
    payload, code = run(args, command=command_str.strip())
    print(dump_yaml(payload))
    return code


if __name__ == "__main__":
    sys.exit(main())
