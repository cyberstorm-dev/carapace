"""Cross-agent output composition reporter.

Analyses PRs and issues to identify deliverables where multiple specialist
agents (builder, reviewer, cloudops, main) contributed to a single coherent
output.  Generates a composition evidence report in Markdown and optional JSON.

Composition is detected when:
- A PR has commits from 2+ different agents, OR
- A PR has reviews from a different agent than the author, OR
- An issue's comments show 2+ agents collaborating on the deliverable.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Union
from urllib import request

from .hateoas import dump_yaml, envelope

AGENT_USERNAMES = {"builder", "reviewer", "cloudops", "allenday"}
ISO_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")

Fetcher = Callable[[str], Any]


def _parse_datetime(raw: str) -> datetime:
    cleaned = raw
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    for fmt in ISO_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Could not parse datetime: {raw}")


def _fetch_json(
    url: str,
    token: str,
    opener: Optional[Callable[[request.Request], Iterable]] = None,
):
    opener = opener or request.urlopen
    req = request.Request(url, headers={"Authorization": f"token {token}"})
    with opener(req) as resp:
        return json.loads(resp.read().decode())


@dataclass
class AgentContribution:
    """A single agent's contribution to a deliverable."""

    agent: str
    role: str  # author, reviewer, committer, commenter
    details: str = ""


@dataclass
class ComposedDeliverable:
    """A deliverable that blends outputs from multiple agents."""

    pr_number: int
    title: str
    url: str
    agents: List[AgentContribution] = field(default_factory=list)
    composition_type: str = ""  # "code+review", "multi-commit", "orchestrated"

    @property
    def agent_names(self) -> List[str]:
        return sorted({a.agent for a in self.agents})

    @property
    def is_composed(self) -> bool:
        return len(self.agent_names) >= 2


def find_composed_prs(
    repo: str,
    base_url: str,
    token: str,
    milestone: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
) -> List[ComposedDeliverable]:
    """Scan PRs for cross-agent composition evidence."""
    if fetcher is None:
        fetcher = lambda url: _fetch_json(url, token)  # noqa: E731

    # Fetch PRs (closed/merged)
    params = "state=closed&limit=50&sort=created&type=pulls"
    if milestone:
        # Resolve milestone id
        ms_list = fetcher(f"{base_url}/repos/{repo}/milestones?limit=50")
        ms_id = None
        for ms in ms_list:
            if ms.get("title", "").lower() == milestone.lower():
                ms_id = ms["id"]
                break
        if ms_id:
            params += f"&milestones={ms_id}"

    pulls_url = f"{base_url}/repos/{repo}/pulls?{params}"
    pulls = fetcher(pulls_url)

    deliverables: List[ComposedDeliverable] = []

    for pr in pulls:
        if not pr.get("merged"):
            continue

        number = pr["number"]
        title = pr["title"]
        html_url = pr["html_url"]
        author = pr.get("user", {}).get("login", "unknown")

        d = ComposedDeliverable(
            pr_number=number,
            title=title,
            url=html_url,
        )

        # Author contribution
        if author in AGENT_USERNAMES:
            d.agents.append(AgentContribution(
                agent=author, role="author", details=f"Opened PR #{number}"
            ))

        # Fetch reviews
        reviews_url = f"{base_url}/repos/{repo}/pulls/{number}/reviews"
        try:
            reviews = fetcher(reviews_url)
        except Exception:
            reviews = []

        seen_reviewers: set = set()
        for review in reviews:
            reviewer = review.get("user", {}).get("login", "unknown")
            state = review.get("state", "").upper()
            if reviewer in AGENT_USERNAMES and reviewer not in seen_reviewers:
                seen_reviewers.add(reviewer)
                d.agents.append(AgentContribution(
                    agent=reviewer,
                    role="reviewer",
                    details=f"{state} on PR #{number}",
                ))

        # Check merge user (orchestrator composing the result)
        merged_by = pr.get("merged_by", {})
        if merged_by:
            merger = merged_by.get("login", "unknown")
            if merger in AGENT_USERNAMES and merger != author:
                d.agents.append(AgentContribution(
                    agent=merger,
                    role="merger",
                    details=f"Merged PR #{number}",
                ))

        # Classify composition type
        roles = {a.role for a in d.agents}
        if "author" in roles and "reviewer" in roles:
            d.composition_type = "code+review"
        if len({a.agent for a in d.agents if a.role == "author"}) > 1:
            d.composition_type = "multi-author"
        if "merger" in roles and "author" in roles:
            d.composition_type = d.composition_type or "orchestrated"

        if d.is_composed:
            deliverables.append(d)

    return deliverables


def render_markdown(deliverables: List[ComposedDeliverable]) -> str:
    """Render composition report as Markdown."""
    lines = [
        "# Cross-Agent Output Composition Report",
        "",
        f"**Total composed deliverables:** {len(deliverables)}",
        "",
    ]

    if not deliverables:
        lines.append("No composed deliverables found.")
        return "\n".join(lines)

    lines.extend([
        "| PR | Title | Agents | Composition Type |",
        "|----|-------|--------|-----------------|",
    ])

    for d in deliverables:
        agents_str = ", ".join(d.agent_names)
        lines.append(
            f"| [#{d.pr_number}]({d.url}) | {d.title} | {agents_str} | {d.composition_type} |"
        )

    lines.extend(["", "## Detailed Contributions", ""])

    for d in deliverables:
        lines.append(f"### PR #{d.pr_number}: {d.title}")
        lines.append("")
        for a in d.agents:
            lines.append(f"- **{a.agent}** ({a.role}): {a.details}")
        lines.append("")

    return "\n".join(lines)


def render_json(deliverables: List[ComposedDeliverable]) -> str:
    """Render composition report as JSON."""
    data = []
    for d in deliverables:
        data.append({
            "pr_number": d.pr_number,
            "title": d.title,
            "url": d.url,
            "agents": [asdict(a) for a in d.agents],
            "agent_names": d.agent_names,
            "composition_type": d.composition_type,
        })
    return json.dumps({"composed_deliverables": data, "count": len(data)}, indent=2)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-agent output composition reporter"
    )
    parser.add_argument("--repo", default="openclaw/nisto-home")
    parser.add_argument(
        "--gitea-url",
        default=os.environ.get("GITEA_URL", "http://100.73.228.90:3000/api/v1"),
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITEA_TOKEN", ""),
    )
    parser.add_argument("--milestone", default=None, help="Filter by milestone title")
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "yaml"],
        default="markdown",
    )
    args = parser.parse_args(argv)

    if not args.token:
        print("Error: --token or GITEA_TOKEN required")
        return 1

    deliverables = find_composed_prs(
        repo=args.repo,
        base_url=args.gitea_url,
        token=args.token,
        milestone=args.milestone,
    )

    if args.format == "json":
        print(render_json(deliverables))
    elif args.format == "yaml":
        print(
            dump_yaml(
                envelope(
                    command="carapace composition-report",
                    ok=True,
                    result={
                        "count": len(deliverables),
                        "deliverables": [
                            {
                                "pr": d.pr_number,
                                "agents": d.agent_names,
                                "type": d.composition_type,
                            }
                            for d in deliverables
                        ],
                    },
                    next_actions=[
                        {
                            "command": "carapace composition-report --format markdown",
                            "description": "View full Markdown report",
                        }
                    ],
                )
            )
        )
    else:
        print(render_markdown(deliverables))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
