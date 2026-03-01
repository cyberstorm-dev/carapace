from typing import List, Dict, Any, Set
import networkx as nx
import re

def is_tan(issue: Dict[str, Any]) -> bool:
    """Check if an issue is a Tan (Source Node)."""
    title = issue.get("title", "").upper()
    labels = {l.get("name").upper() for l in issue.get("labels", []) if l.get("name")}
    return "[TAN]" in title or "[TANNING]" in title or "TAN" in labels

def is_molt(issue: Dict[str, Any]) -> bool:
    """Check if an issue is a Molt (Sink Node)."""
    title = issue.get("title", "").upper()
    labels = {l.get("name").upper() for l in issue.get("labels", []) if l.get("name")}
    return "[TERMINAL]" in title or "[MOLT]" in title or "MOLT" in labels

def get_active_subgraph(graph: nx.DiGraph) -> Set[int]:
    """
    Returns the set of node IDs that are part of the 'Active Subgraph'.
    An issue is active if it is:
    1) A descendant of an OPEN Tan node.
    2) An ancestor of an OPEN Molt node.
    """
    tan_nodes = [n for n, d in graph.nodes(data=True) if "TAN" in [l.upper() for l in d.get("labels", [])]]
    molt_nodes = [n for n, d in graph.nodes(data=True) if "MOLT" in [l.upper() for l in d.get("labels", [])]]
    
    if not tan_nodes or not molt_nodes:
        # Fallback: if no Tans/Molts marked as such, maybe the whole graph is active?
        # No, better to be strict as per #172.
        return set()

    active_nodes = set()
    for node in graph.nodes():
        # nx.has_path is O(V+E), this might be slow for huge graphs but fine for current scale.
        # Efficient way: find all nodes reachable from tan_nodes, and all nodes that can reach molt_nodes.
        # The intersection is the subgraph.
        pass

    # Optimization:
    reachable_from_tans = set()
    for tan in tan_nodes:
        reachable_from_tans.update(nx.descendants(graph, tan))
        reachable_from_tans.add(tan)

    can_reach_molts = set()
    for molt in molt_nodes:
        can_reach_molts.update(nx.ancestors(graph, molt))
        can_reach_molts.add(molt)

    return reachable_from_tans.intersection(can_reach_molts)

def calculate_priority(graph: nx.DiGraph, ready_nodes: List[int]) -> Dict[int, int]:
    """Calculates priority scores based on number of descendants."""
    scores = {}
    for node in ready_nodes:
        if node not in graph:
            scores[node] = 0
            continue
        descendants = nx.descendants(graph, node)
        scores[node] = len(descendants)
    return scores
