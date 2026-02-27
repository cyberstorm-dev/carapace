from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import networkx as nx

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
    "missing_parent_dependency": TIER_HARD,
    "leaf_without_needs_pr": TIER_HARD,
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


def _dependencies_for(issue: Dict[str, Any]) -> List[int]:
    deps = issue.get("dependencies") or issue.get("depends_on") or []
    return [int(d) for d in deps]


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


def build_graph(issues: Iterable[Dict[str, Any]]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for issue in issues:
        number = int(issue["number"])
        graph.add_node(
            number,
            labels=_labels_for(issue),
            assignee=issue.get("assignee"),
            milestone=_milestone_id(issue),
            synthetic=issue.get("synthetic", False),
        )

    for issue in issues:
        number = int(issue["number"])
        for dep in _dependencies_for(issue):
            if dep not in graph:
                graph.add_node(dep, labels=set(), assignee=None, milestone=None, synthetic=True)
            graph.add_edge(dep, number)

    return graph


def _reachable_from_any(graph: nx.DiGraph, sources: List[int], target: int) -> bool:
    return any(nx.has_path(graph, src, target) for src in sources)


def _reaches_any(graph: nx.DiGraph, source: int, sinks: List[int]) -> bool:
    return any(nx.has_path(graph, source, sink) for sink in sinks)


def _resolve_tier(check: str, config: Config) -> str:
    tier = config.check_tiers.get(check) or DEFAULT_CHECK_TIERS.get(check)
    return tier if tier in TIER_ORDER else TIER_ADVISORY


def _add_message(messages: List[ValidationMessage], check: str, message: str, config: Config) -> None:
    messages.append(ValidationMessage(check=check, tier=_resolve_tier(check, config), message=message))


def validate_issues(
    issues: Iterable[Dict[str, Any]], config: Config, tan_next_phase: Optional[Iterable[Dict[str, Any]]] = None
) -> List[ValidationMessage]:
    """
    Validate issues for a given phase. Expects tan in the next phase (phase+1) when provided via tan_next_phase.

    Returns a list of ValidationMessage entries tagged with tiers.
    """

    messages: List[ValidationMessage] = []
    graph = build_graph(issues)

    needs_label = config.labels.get("needs_pr", "needs-pr")
    molt_label = config.labels.get("molt", "molt")
    tan_label = config.labels.get("tan", "tan")

    molt_nodes = [n for n, data in graph.nodes(data=True) if molt_label in data["labels"]]
    tan_nodes = [n for n, data in graph.nodes(data=True) if tan_label in data["labels"]]
    needs_pr_nodes = [n for n, data in graph.nodes(data=True) if needs_label in data["labels"]]
    
    current_phase_tan_nodes = list(tan_nodes)

    if tan_next_phase:
        for issue in tan_next_phase:
            n = int(issue["number"])
            graph.add_node(
                n,
                labels=_labels_for(issue),
                assignee=issue.get("assignee"),
                milestone=_milestone_id(issue),
                synthetic=True,
            )
            for dep in _dependencies_for(issue):
                if dep not in graph:
                    graph.add_node(dep, labels=set(), assignee=None, milestone=None, synthetic=True)
                graph.add_edge(dep, n)
            tan_nodes.append(n)

    if not molt_nodes:
        _add_message(messages, "missing_molt_issue", "No molt-labeled issues found", config)
    elif len(molt_nodes) > 1:
        _add_message(messages, "multiple_molt_issues", f"Found multiple molt-labeled issues: {molt_nodes}", config)

    if not current_phase_tan_nodes:
        _add_message(messages, "missing_tan_issue", "No tan-labeled issues found in this phase", config)
    elif len(current_phase_tan_nodes) > 1:
        _add_message(messages, "multiple_tan_issues", f"Found multiple tan-labeled issues in this phase: {current_phase_tan_nodes}", config)

    if not needs_pr_nodes:
        _add_message(messages, "missing_needs_pr_issue", f"No `{needs_label}`-labeled issues found", config)

    for node, data in graph.nodes(data=True):
        if data.get("synthetic") or node in config.exempt_issues:
            continue

        labels = data.get("labels", set())
        has_needs_pr = needs_label in labels
        is_molt = molt_label in labels
        is_tan = tan_label in labels
        is_helpdesk = "helpdesk" in labels

        if data.get("assignee") is None:
            _add_message(messages, "missing_assignee", f"Issue #{node} is missing an assignee (queued for assignment)", config)

        if is_helpdesk:
            continue

        if _milestone_id({"milestone": data.get("milestone")}) is None:
            _add_message(messages, "missing_milestone", f"Issue #{node} is missing a milestone assignment", config)

        if not is_molt and not is_tan and not has_needs_pr and graph.in_degree(node) == 0:
            _add_message(messages, "missing_parent_dependency", f"Issue #{node} has no dependency/parent", config)

        if not is_molt and not is_tan and not has_needs_pr and graph.out_degree(node) == 0:
            _add_message(
                messages,
                "leaf_without_needs_pr",
                f"Issue #{node} is not depended on and is missing `{needs_label}` label",
                config,
            )

        if tan_nodes and not _reachable_from_any(graph, tan_nodes, node):
            _add_message(
                messages,
                "tan_reachability",
                f"Issue #{node} is not reachable from a tan-labeled issue (mid-phase work?)",
                config,
            )

        if molt_nodes and not _reaches_any(graph, node, molt_nodes):
            _add_message(messages, "molt_reachability", f"Issue #{node} does not reach a molt-labeled issue", config)

        if not is_molt and molt_nodes:
            for molt in molt_nodes:
                if graph.has_edge(molt, node):
                    _add_message(messages, "molt_as_dependency", f"Issue #{node} incorrectly depends on terminal molt issue #{molt}", config)

    return messages
