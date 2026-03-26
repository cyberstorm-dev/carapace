from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import networkx as nx

from carapace.issue_ref import IssueRef, parse_dependency_refs, parse_issue_ref
from .config import Config

TIER_HARD = "hard"
TIER_ADVISORY = "advisory"
TIER_INFO = "info"

TIER_ORDER = [TIER_HARD, TIER_ADVISORY, TIER_INFO]

DEFAULT_CHECK_TIERS: Dict[str, str] = {
    "missing_molt_issue": TIER_HARD,
    "missing_tan_issue": TIER_HARD,
    "multiple_tan_issues": TIER_HARD,
    "missing_needs_pr_issue": TIER_HARD,
    "missing_milestone": TIER_HARD,
    "missing_assignee": TIER_ADVISORY,
    "tan_reachability": TIER_ADVISORY,
    "molt_reachability": TIER_HARD,
    "molt_as_dependency": TIER_HARD,
    "multiple_molt_issues": TIER_HARD,
    "base_branch": TIER_HARD,
}


@dataclass
class ValidationMessage:
    check: str
    tier: str
    message: str


def _labels_for(issue: Dict[str, Any]) -> set[str]:
    return {label.get("name") for label in issue.get("labels", []) if label.get("name")}


def _dependencies_for(issue: Dict[str, Any], default_repo: str) -> List[IssueRef]:
    deps = issue.get("dependencies") or issue.get("depends_on") or []
    return parse_dependency_refs(deps, default_repo=default_repo)


def _milestone_id(issue: Dict[str, Any]) -> Optional[int]:
    milestone = issue.get("milestone")
    if milestone is None:
        return None
    if isinstance(milestone, dict):
        if milestone.get("id") is not None:
            return int(milestone.get("id"))
        if milestone.get("index") is not None:
            return int(milestone.get("index"))
    try:
        return int(milestone)
    except (TypeError, ValueError):
        return None


def build_graph(issues: Iterable[Dict[str, Any]], default_repo: str = "local") -> nx.DiGraph:
    graph = nx.DiGraph()
    for issue in issues:
        issue_repo = issue.get("repo", default_repo)
        issue_ref = parse_issue_ref(issue.get("number"), default_repo=issue_repo)
        if issue_ref is None:
            continue
        graph.add_node(
            issue_ref,
            labels=_labels_for(issue),
            assignee=issue.get("assignee"),
            milestone=_milestone_id(issue),
            state=issue.get("state", "open"),
            synthetic=issue.get("synthetic", False),
        )

    for issue in issues:
        issue_repo = issue.get("repo", default_repo)
        issue_ref = parse_issue_ref(issue.get("number"), default_repo=issue_repo)
        if issue_ref is None:
            continue
        for dep in _dependencies_for(issue, default_repo=issue_repo):
            if dep not in graph:
                graph.add_node(dep, labels=set(), assignee=None, milestone=None, state='open', synthetic=True)
            graph.add_edge(dep, issue_ref)

    return graph


def _reachable_from_any(graph: nx.DiGraph, sources: List[IssueRef], target: IssueRef) -> bool:
    return any(nx.has_path(graph, src, target) for src in sources)


def _reaches_any(graph: nx.DiGraph, source: IssueRef, sinks: List[IssueRef]) -> bool:
    return any(nx.has_path(graph, source, sink) for sink in sinks)


def _resolve_tier(check: str, config: Config) -> str:
    tier = config.check_tiers.get(check) or DEFAULT_CHECK_TIERS.get(check)
    return tier if tier in TIER_ORDER else TIER_ADVISORY


def _add_message(messages: List[ValidationMessage], check: str, message: str, config: Config) -> None:
    messages.append(ValidationMessage(check=check, tier=_resolve_tier(check, config), message=message))


def _node_label(node: IssueRef, default_repo: str) -> str:
    return node.display(local_repo=default_repo)


def validate_issues(
    issues: Iterable[Dict[str, Any]],
    config: Config,
    tan_next_phase: Optional[Iterable[Dict[str, Any]]] = None,
    default_repo: str = "local",
) -> List[ValidationMessage]:
    """
    Validate issues for a given phase. Expects tan in the next phase (phase+1) when provided via tan_next_phase.

    Returns a list of ValidationMessage entries tagged with tiers.
    """

    messages: List[ValidationMessage] = []
    graph = build_graph(issues, default_repo=default_repo)

    needs_label = config.labels.get("needs_pr", "needs-pr")
    molt_label = config.labels.get("molt", "molt")
    tan_label = config.labels.get("tan", "tan")

    molt_nodes = [n for n, data in graph.nodes(data=True) if molt_label in data["labels"]]
    tan_nodes = [n for n, data in graph.nodes(data=True) if tan_label in data["labels"]]
    needs_pr_nodes = [n for n, data in graph.nodes(data=True) if needs_label in data["labels"]]
    current_phase_tan_nodes = list(tan_nodes)

    if tan_next_phase:
        repo_for_tan = default_repo
        for issue in tan_next_phase:
            repo_for_tan = issue.get("repo", repo_for_tan)
            n = parse_issue_ref(issue.get("number"), default_repo=repo_for_tan)
            if n is None:
                continue
            issue_repo = issue.get("repo", repo_for_tan)
            graph.add_node(
                n,
                labels=_labels_for(issue),
                assignee=issue.get("assignee"),
                milestone=_milestone_id(issue),
                state=issue.get("state", "open"),
                synthetic=True,
            )
            for dep in _dependencies_for(issue, default_repo=issue_repo):
                if dep not in graph:
                    graph.add_node(dep, labels=set(), assignee=None, milestone=None, state='open', synthetic=True)
                graph.add_edge(dep, n)
            tan_nodes.append(n)

    if not molt_nodes:
        _add_message(messages, "missing_molt_issue", "No molt-labeled issues found", config)
    elif len(molt_nodes) > 1:
        _add_message(messages, "multiple_molt_issues", f"Found multiple molt-labeled issues: {molt_nodes}", config)

    if not current_phase_tan_nodes:
        _add_message(messages, "missing_tan_issue", "No tan-labeled issues found in this phase", config)
    elif len(current_phase_tan_nodes) > 1:
        _add_message(
            messages,
            "multiple_tan_issues",
            f"Found multiple tan-labeled issues in this phase: {current_phase_tan_nodes}",
            config,
        )

    if not needs_pr_nodes:
        _add_message(messages, "missing_needs_pr_issue", f"No `{needs_label}`-labeled issues found", config)

    for node, data in graph.nodes(data=True):
        if data.get("synthetic") or node in config.exempt_issues:
            continue
        
        is_closed = data.get("state") == "closed"

        labels = data.get("labels", set())
        has_needs_pr = needs_label in labels
        is_molt = molt_label in labels
        is_tan = tan_label in labels
        is_helpdesk = "helpdesk" in labels

        if not is_closed and data.get("assignee") is None:
            _add_message(
                messages,
                "missing_assignee",
                f"Issue {_node_label(node, default_repo)} is missing an assignee (queued for assignment)",
                config,
            )

        if is_helpdesk:
            continue

        if _milestone_id({"milestone": data.get("milestone")}) is None:
            _add_message(messages, "missing_milestone", f"Issue {_node_label(node, default_repo)} is missing a milestone assignment", config)





        if tan_nodes and not _reachable_from_any(graph, tan_nodes, node):
            _add_message(
                messages,
                "tan_reachability",
                f"Issue {_node_label(node, default_repo)} is not reachable from a tan-labeled issue (mid-phase work?)",
                config,
            )

        if molt_nodes and not _reaches_any(graph, node, molt_nodes):
            _add_message(messages, "molt_reachability", f"Issue {_node_label(node, default_repo)} does not reach a molt-labeled issue", config)

        if not is_molt and molt_nodes:
            for molt in molt_nodes:
                if graph.has_edge(molt, node):
                    _add_message(
                        messages,
                        "molt_as_dependency",
                        (
                            f"Issue {_node_label(node, default_repo)} incorrectly depends on terminal molt issue "
                            f"{_node_label(molt, default_repo)}"
                        ),
                        config,
                    )

    return messages
