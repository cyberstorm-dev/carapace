"""Visualize the phase dependency graph as text or Mermaid."""

import argparse
import base64
import json
import re
import sys
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from .cli import (
    DEFAULT_GITEA_URL,
    PAGE_SIZE,
    _fetch_dependencies,
    _phase_of_issue,
    build_auth_headers,
    fetch_all_issues,
    fetch_open_pulls,
)
from .config import load_config
from .validation import _labels_for, build_graph


def _fetch_pulls(gitea_url: str, owner: str, name: str, token: str) -> List[Dict[str, Any]]:
    """Fetch all PRs (open + closed/merged)."""
    headers = {"Authorization": f"token {token}"}
    pulls: List[Dict[str, Any]] = []
    for state in ("open", "closed"):
        page = 1
        while True:
            url = f"{gitea_url.rstrip('/')}/api/v1/repos/{owner}/{name}/pulls?state={state}&page={page}&limit={PAGE_SIZE}"
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=30) as resp:
                batch = json.loads(resp.read().decode())
            pulls.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1
    return pulls


def _extract_closes(body: str) -> List[int]:
    """Extract issue numbers from 'Closes #N' / 'Fixes #N' patterns."""
    if not body:
        return []
    return [int(m) for m in re.findall(r'(?:closes|fixes|resolves)\s+#(\d+)', body, re.IGNORECASE)]


def _escape_mermaid_label(label: str) -> str:
    return label.replace("\"", "\\\"")


def _classes_for_node(
    node: int,
    *,
    graph,
    issue_map: Dict[int, Dict[str, Any]],
    tan_label: str,
    molt_label: str,
) -> List[str]:
    labels = graph.nodes[node].get("labels", set())
    issue = issue_map.get(node, {})
    state = issue.get("state", "open")

    if molt_label in labels:
        return ["molt"]
    if tan_label in labels:
        return ["tan"]
    if state == "closed":
        return ["closed"]
    return ["open"]


def _render_mermaid(
    *,
    phase: int,
    graph,
    issue_map: Dict[int, Dict[str, Any]],
    pr_map: Dict[int, List[Dict[str, Any]]],
    open_pulls: List[Dict[str, Any]],
    tan_label: str,
    molt_label: str,
) -> str:
    lines = [f"%% Phase {phase} dependency graph", "graph LR"]

    # Add Legend
    lines.extend([
        "    subgraph Legend",
        "        l1[Tan/Root]:::tan",
        "        l2[Molt/Terminal]:::molt",
        "        l3[Closed/Done]:::closed",
        "        l4[Open/Active]:::open",
        "    end"
    ])

    # Grouping logic
    external_prev = []
    external_next = []
    current_phase = []
    
    for node in sorted(graph.nodes):
        issue = issue_map.get(node, {})
        node_phase = _phase_of_issue(issue)
        if node_phase is None:
            current_phase.append(node)
        elif node_phase < phase:
            external_prev.append(node)
        elif node_phase > phase:
            external_next.append(node)
        else:
            current_phase.append(node)

    def write_nodes(node_list, lines):
        for node in node_list:
            issue = issue_map.get(node, {})
            title = (issue.get("title") or "Unknown").strip()[:70]
            node_phase = _phase_of_issue(issue)
            phase_prefix = f" (P{node_phase})" if node_phase and node_phase != phase else ""
            label = _escape_mermaid_label(f"#{node}{phase_prefix} {title}")
            lines.append(f"    i{node}[\"{label}\"]")

    if external_prev:
        lines.append(f"    subgraph Previous_Phases")
        write_nodes(external_prev, lines)
        lines.append("    end")

    if current_phase:
        lines.append(f"    subgraph Phase_{phase}_Issues")
        write_nodes(current_phase, lines)
        lines.append("    end")

    if external_next:
        lines.append(f"    subgraph Future_Phases")
        write_nodes(external_next, lines)
        lines.append("    end")

    # Open PRs Subgraph
    if open_pulls:
        lines.append("    subgraph Open_PRs")
        for pr in open_pulls:
            pr_num = pr["number"]
            title = (pr.get("title") or "Unknown").strip()[:50]
            label = _escape_mermaid_label(f"PR#{pr_num} {title}")
            lines.append(f"    p{pr_num}[[\"{label}\"]]")
        lines.append("    end")

    # Add Edges
    for src, dst in sorted(graph.edges()):
        lines.append(f"    i{src} --> i{dst}")

    # Link PRs to their closed issues (PR blocks the Issue from closing)
    for pr in open_pulls:
        pr_num = pr["number"]
        closes = _extract_closes(pr.get("body", "") or "")
        for issue_num in closes:
            if issue_num in graph.nodes:
                lines.append(f"    p{pr_num} -.-> i{issue_num}")

    lines.extend(
        [
            "    classDef open fill:#ef4444,stroke:#7f1d1d,color:#fff;",
            "    classDef closed fill:#22c55e,stroke:#166534,color:#fff;",
            "    classDef tan fill:#f59e0b,stroke:#92400e,color:#fff;",
            "    classDef molt fill:#3b82f6,stroke:#1d4ed8,color:#fff;",
        ]
    )

    for node in sorted(graph.nodes):
        classes = _classes_for_node(
            node, graph=graph, issue_map=issue_map, tan_label=tan_label, molt_label=molt_label
        )
        lines.append(f"    class i{node} {','.join(classes)}")

    return "\n".join(lines)


def _render_text(
    *,
    phase: int,
    graph,
    issue_map: Dict[int, Dict[str, Any]],
    pr_map: Dict[int, List[Dict[str, Any]]],
    open_pulls: List[Dict[str, Any]],
    tan_nodes: List[int],
    molt_nodes: List[int],
    tan_label: str,
    molt_label: str,
    needs_pr: str,
) -> str:
    def node_label(n: int) -> str:
        labels = graph.nodes[n].get("labels", set())
        issue = issue_map.get(n, {})
        title = issue.get("title", "?")[:50]
        state = issue.get("state", "?")
        state_icon = "✅" if state == "closed" else "🔴"
        assignee = ""
        if issue.get("assignee") and isinstance(issue["assignee"], dict):
            assignee = f" @{issue['assignee'].get('login', '?')}"

        role = ""
        if molt_label in labels:
            role = " [MOLT/sink]"
        elif tan_label in labels:
            role = " [TAN/source]"
        elif needs_pr in labels:
            role = " [needs-pr]"

        return f"#{n} {state_icon} {title}{role}{assignee}"

    lines = [f"Phase {phase} — DAG visualization", ""]

    # Print by layer: tan (source) → work → molt (sink)
    def _layers_from_sources(g) -> tuple[List[List[int]], List[int]]:
        visited = set()
        layers: List[List[int]] = []
        frontier = set(tan_nodes)
        while frontier:
            layer = sorted(frontier)
            layers.append(layer)
            visited.update(frontier)
            next_layer = set()
            for node in frontier:
                for succ in g.successors(node):
                    if succ not in visited:
                        next_layer.add(succ)
            frontier = next_layer
        orphans = sorted(n for n in g.nodes if n not in visited)
        return layers, orphans

    layers, orphans = _layers_from_sources(graph)

    for i, layer in enumerate(layers):
        if i == 0:
            label = "SOURCE (tan)"
        elif any(n in molt_nodes for n in layer):
            label = "SINK (molt)"
        else:
            label = f"LAYER {i}"
        lines.append(f"── {label} ──")
        for n in layer:
            lines.append(f"  {node_label(n)}")
            # Show linked PRs
            if n in pr_map:
                for pr in sorted(pr_map[n], key=lambda p: int(p["number"])):
                    pr_state = "merged" if pr.get("merged") else pr.get("state", "?")
                    pr_icon = "🟣" if pr_state == "merged" else "🟢" if pr_state == "open" else "⚫"
                    lines.append(f"    📎 PR #{pr['number']} [{pr_icon} {pr_state}] {pr['title'][:40]}")
            # Show edges
            for succ in sorted(graph.successors(n)):
                lines.append(f"    └→ #{succ}")
        lines.append("")

    if orphans:
        lines.append("── UNREACHABLE/ORPHAN ──")
        for n in orphans:
            lines.append(f"  {node_label(n)}")
            if n in pr_map:
                for pr in sorted(pr_map[n], key=lambda p: int(p["number"])):
                    pr_state = "merged" if pr.get("merged") else pr.get("state", "?")
                    pr_icon = "🟣" if pr_state == "merged" else "🟢" if pr_state == "open" else "⚫"
                    lines.append(f"    📎 PR #{pr['number']} [{pr_icon} {pr_state}] {pr['title'][:40]}")
            for succ in sorted(graph.successors(n)):
                lines.append(f"    └→ #{succ}")
        lines.append("")

    # Open PRs
    if open_pulls:
        lines.append("── OPEN PRs ──")
        for pr in sorted(open_pulls, key=lambda p: int(p.get("number", 0))):
            num = pr["number"]
            title = pr.get("title", "?")[:50]
            base = pr.get("base", {}).get("ref", "?")
            head = pr.get("head", {}).get("ref", "?")
            user = pr.get("user", {}).get("login", "?")
            body = pr.get("body", "") or ""
            import re as _re

            linked = _re.findall(r'(?:closes|fixes|resolves)\s+#(\d+)', body, _re.IGNORECASE)
            linked_str = f" → closes {', '.join('#'+n for n in linked)}" if linked else ""
            base_warn = " ⚠️ WRONG BASE" if base != "dev" else ""
            lines.append(f"  PR #{num} [{head} → {base}{base_warn}] @{user}: {title}{linked_str}")
        lines.append("")

    # Edge summary
    lines.append(f"Edges: {graph.number_of_edges()}  Nodes: {graph.number_of_nodes()}")
    lines.append(f"Molt (sink): {molt_nodes}  Tan (source): {tan_nodes}")

    return "\n".join(lines)


def _render_link(mermaid_code: str) -> str:
    """Encode Mermaid code into a mermaid.ink URL."""
    # Use urlsafe_b64encode and remove padding '=' as expected by some decoders
    encoded = base64.urlsafe_b64encode(mermaid_code.encode("utf-8")).decode("utf-8").rstrip("=")
    return f"https://mermaid.ink/img/{encoded}"


def _render_shortlink(url: str) -> str:
    """Shorten a URL using TinyURL."""
    try:
        api_url = f"https://tinyurl.com/api-create.php?url={parse.quote(url)}"
        with request.urlopen(api_url, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except Exception as exc:
        print(f"Warning: Failed to shorten URL: {exc}", file=sys.stderr)
        return url


def viz_phase(
    *,
    phase: int,
    gitea_url: str,
    repo: str,
    token: str,
    config_path: str,
    output_format: str = "text",
) -> str:
    config = load_config(config_path)
    owner, name = repo.split("/")
    headers, _ = build_auth_headers(token)
    all_issues = fetch_all_issues(gitea_url, repo, headers)
    all_pulls = _fetch_pulls(gitea_url, owner, name, token)

    tan_label = config.labels.get("tan", "tan")
    molt_label = config.labels.get("molt", "molt")
    needs_pr = config.labels.get("needs_pr", "needs-pr")

    phase_issues = [i for i in all_issues if _phase_of_issue(i) == phase]
    tan_next = [
        i
        for i in all_issues
        if _phase_of_issue(i) == phase + 1 and any(l.get("name") == tan_label for l in i.get("labels", []))
    ]

    for issue in phase_issues + tan_next:
        issue["dependencies"] = _fetch_dependencies(gitea_url, owner, name, headers, issue["number"])

    for issue in tan_next:
        issue["synthetic"] = True

    graph = build_graph(phase_issues + tan_next)

    phase_numbers = {int(i["number"]) for i in phase_issues}
    pr_map: Dict[int, List[Dict[str, Any]]] = {}
    for pr in all_pulls:
        closes = _extract_closes(pr.get("body", "") or "")
        for issue_num in closes:
            if issue_num in phase_numbers:
                pr_map.setdefault(issue_num, []).append(pr)

    issue_map: Dict[int, Dict[str, Any]] = {int(i["number"]): i for i in phase_issues + tan_next}

    # Fetch titles for any dependency nodes not in the current phase or next tan
    missing_titles = [n for n in graph.nodes if n not in issue_map]
    if missing_titles:
        for node_id in missing_titles:
            try:
                url = f"{gitea_url.rstrip('/')}/api/v1/repos/{owner}/{name}/issues/{node_id}"
                req = request.Request(url, headers=headers)
                with request.urlopen(req, timeout=10) as resp:
                    issue_data = json.loads(resp.read().decode())
                    issue_map[node_id] = issue_data
            except Exception:
                continue

    tan_nodes = [n for n in graph.nodes if tan_label in graph.nodes[n].get("labels", set())]
    molt_nodes = [n for n in graph.nodes if molt_label in graph.nodes[n].get("labels", set())]

    open_pulls = fetch_open_pulls(gitea_url, repo, headers)

    if output_format in ("mermaid", "link", "shortlink"):
        mermaid = _render_mermaid(
            phase=phase,
            graph=graph,
            issue_map=issue_map,
            pr_map=pr_map,
            open_pulls=open_pulls,
            tan_label=tan_label,
            molt_label=molt_label,
        )
        if output_format == "link":
            return _render_link(mermaid)
        if output_format == "shortlink":
            return _render_shortlink(_render_link(mermaid))
        return mermaid

    return _render_text(
        phase=phase,
        graph=graph,
        issue_map=issue_map,
        pr_map=pr_map,
        open_pulls=open_pulls,
        tan_nodes=tan_nodes,
        molt_nodes=molt_nodes,
        tan_label=tan_label,
        molt_label=molt_label,
        needs_pr=needs_pr,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Visualize phase dependency graph")
    parser.add_argument("--phase", type=int, required=True, help="Phase number")
    parser.add_argument("--config", required=True, help="Path to validator.yaml")
    parser.add_argument("--repo", required=True, help="<owner>/<repo>")
    parser.add_argument("--token", required=True, help="Gitea token")
    parser.add_argument("--gitea-url", default=DEFAULT_GITEA_URL)
    parser.add_argument("--format", choices=["text", "mermaid", "link", "shortlink"], default="text", help="Output format")
    args = parser.parse_args(argv or sys.argv[1:])

    try:
        output = viz_phase(
            phase=args.phase,
            gitea_url=args.gitea_url,
            repo=args.repo,
            token=args.token,
            config_path=args.config,
            output_format=args.format,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
