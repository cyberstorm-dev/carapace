from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, Optional


STATUS_MAP = {
    "success": 1,
    "passed": 1,
    "failure": 0,
    "failed": 0,
    "error": 0,
    "killed": 0,
    "skipped": -1,
    "canceled": -1,
    "cancelled": -1,
}


@dataclass
class PipelineContext:
    repo: str
    owner: str
    name: str
    branch: str
    event: str
    status: str
    pipeline: str
    started: Optional[int]
    finished: Optional[int]
    duration_seconds: Optional[int]


def _first(env: Dict[str, str], keys: Iterable[str], default: Optional[str] = None) -> Optional[str]:
    for key in keys:
        value = env.get(key)
        if value:
            return value
    return default


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def status_to_value(status: str) -> int:
    return STATUS_MAP.get(status.lower(), 0)


def collect_context(env: Optional[Dict[str, str]] = None) -> PipelineContext:
    env = env or os.environ

    repo = _first(env, ["CI_REPO", "DRONE_REPO"], "unknown/unknown")
    owner = _first(env, ["CI_REPO_OWNER", "DRONE_REPO_OWNER"], repo.split("/")[0])
    name = _first(env, ["CI_REPO_NAME", "DRONE_REPO_NAME"], repo.split("/")[-1])
    branch = _first(
        env,
        ["CI_COMMIT_BRANCH", "CI_BRANCH", "DRONE_COMMIT_BRANCH", "DRONE_BRANCH"],
        "unknown",
    )
    event = _first(env, ["CI_PIPELINE_EVENT", "CI_BUILD_EVENT", "DRONE_BUILD_EVENT"], "unknown")
    status = _first(env, ["CI_PIPELINE_STATUS", "CI_BUILD_STATUS", "DRONE_BUILD_STATUS"], "unknown")
    pipeline = _first(
        env,
        ["CI_PIPELINE_NUMBER", "CI_BUILD_NUMBER", "DRONE_BUILD_NUMBER", "CI_PIPELINE_ID", "DRONE_BUILD_ID"],
        "unknown",
    )

    started = _parse_int(
        _first(env, ["CI_PIPELINE_STARTED", "CI_BUILD_STARTED", "DRONE_BUILD_STARTED"])
    )
    finished = _parse_int(
        _first(env, ["CI_PIPELINE_FINISHED", "CI_BUILD_FINISHED", "DRONE_BUILD_FINISHED"])
    )
    duration = _parse_int(
        _first(env, ["CI_PIPELINE_DURATION", "CI_BUILD_DURATION", "DRONE_BUILD_DURATION"])
    )
    if duration is None and started is not None and finished is not None:
        duration = max(0, finished - started)

    return PipelineContext(
        repo=repo,
        owner=owner,
        name=name,
        branch=branch,
        event=event,
        status=status,
        pipeline=pipeline,
        started=started,
        finished=finished,
        duration_seconds=duration,
    )


def _sanitize_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\"", "\\\"")


def _render_labels(ctx: PipelineContext) -> str:
    labels = {
        "repo": ctx.repo,
        "owner": ctx.owner,
        "branch": ctx.branch,
        "event": ctx.event,
        "pipeline": ctx.pipeline,
    }
    # Stable order for tests
    label_parts = [f'{key}="{_sanitize_label(value)}"' for key, value in sorted(labels.items())]
    return ",".join(label_parts)


def format_metrics(ctx: PipelineContext) -> str:
    labels = _render_labels(ctx)
    status_value = status_to_value(ctx.status)

    lines = [
        "# HELP woodpecker_pipeline_status Pipeline status (1=success,0=failure,-1=skipped)",
        "# TYPE woodpecker_pipeline_status gauge",
        f"woodpecker_pipeline_status{{{labels}}} {status_value}",
    ]

    if ctx.duration_seconds is not None:
        lines.extend(
            [
                "# HELP woodpecker_pipeline_duration_seconds Pipeline duration in seconds",
                "# TYPE woodpecker_pipeline_duration_seconds gauge",
                f"woodpecker_pipeline_duration_seconds{{{labels}}} {ctx.duration_seconds}",
            ]
        )

    return "\n".join(lines) + "\n"


def _push_to_gateway(url: str, body: str, basic_auth: Optional[str], timeout: int = 5) -> None:
    data = body.encode()
    req = urllib.request.Request(url, data=data, method="PUT")
    if basic_auth:
        req.add_header("Authorization", f"Basic {basic_auth}")
    req.add_header("Content-Type", "text/plain; version=0.0.4")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        resp.read()


def should_skip_push(env: Dict[str, str], allow_owners: set[str]) -> bool:
    opt_out = env.get("CI_METRICS_OPTOUT") or env.get("CI_METRICS_OPT_OUT")
    if opt_out and opt_out.lower() not in {"0", "false", "no"}:
        return True

    owner = _first(env, ["CI_REPO_OWNER", "DRONE_REPO_OWNER"], "")
    if owner and owner not in allow_owners:
        return True
    return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Woodpecker CI metrics emitter")
    parser.add_argument("--pushgateway-url", default=os.environ.get("CI_PROM_PUSHGATEWAY_URL"))
    parser.add_argument("--job", default=os.environ.get("CI_PROM_PUSHGATEWAY_JOB", "woodpecker"))
    parser.add_argument("--instance", default=os.environ.get("CI_PROM_PUSHGATEWAY_INSTANCE"))
    parser.add_argument(
        "--allow-owner",
        action="append",
        default=os.environ.get("CI_METRICS_ALLOW_OWNER", "openclaw").split(","),
        help="Allowed repository owners (skip if different)",
    )
    parser.add_argument(
        "--basic-auth",
        default=os.environ.get("CI_PROM_PUSHGATEWAY_BASIC_AUTH"),
        help="Base64-encoded Basic auth token (not logged)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print metrics without pushing")
    args = parser.parse_args(argv)

    allow_owners = {owner for owner in args.allow_owner if owner}
    env = os.environ

    if should_skip_push(env, allow_owners=allow_owners):
        print("ci-metrics: skipping push (opt-out or disallowed owner)")
        return 0

    ctx = collect_context(env)
    metrics_body = format_metrics(ctx)

    push_url = args.pushgateway_url
    if args.instance:
        push_url = f"{push_url}/metrics/job/{urllib.parse.quote(args.job)}/instance/{urllib.parse.quote(args.instance)}" if push_url else None
    elif push_url:
        push_url = f"{push_url}/metrics/job/{urllib.parse.quote(args.job)}"

    if args.dry_run or not push_url:
        print("ci-metrics: dry-run (no pushgateway configured)")
        sys.stdout.write(metrics_body)
        return 0

    try:
        _push_to_gateway(push_url, metrics_body, args.basic_auth)
        print(f"ci-metrics: pushed metrics to {push_url}")
    except urllib.error.URLError as exc:  # pragma: no cover
        print(f"ci-metrics: WARNING - failed to push metrics: {exc}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
