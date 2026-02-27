import argparse
from typing import Dict, Any, List, Optional
import os
import traceback
import sys
import re

import networkx as nx

from carapace.hateoas import envelope, dump_yaml
from carapace.gt import GiteaClient, GiteaAPIError
from carapace.scheduler import Scheduler
from carapace.worker.pool import WorkerPool, APIKeyPool
from carapace.worker.host import HostWorker
from carapace.validator.cli import build_auth_headers, fetch_all_issues, _phase_of_issue
from carapace.validator.validation import build_graph

def _calculate_priority(graph: nx.DiGraph, ready_nodes: List[int]) -> Dict[int, int]:
    scores = {}
    for node in ready_nodes:
        if node not in graph:
            scores[node] = 0
            continue
        descendants = nx.descendants(graph, node)
        scores[node] = len(descendants)
    return scores

def run(args: argparse.Namespace) -> int:
    try:
        url = args.gitea_url or os.environ.get("GITEA_URL", "http://100.73.228.90:3000")
        token = args.token or os.environ.get("GITEA_TOKEN")
        repo = args.repo or os.environ.get("GITEA_REPO", "openclaw/nisto-home")
        
        if not token:
            print(dump_yaml(envelope(command="carapace queue", ok=False, error={"message": "Missing GITEA_TOKEN"})))
            return 1
            
        client = GiteaClient(url, token, repo)
        dummy_pool = WorkerPool(HostWorker(), APIKeyPool([]), max_parallel=1)
        scheduler = Scheduler(client, dummy_pool, milestone=str(args.milestone))
        
        # --- PHASE RESOLUTION ---
        # Get the milestone details to resolve the phase number
        try:
            milestone_data = client._request("GET", f"milestones/{args.milestone}")
            m = re.search(r"phase\s+(\d+)", milestone_data.get("title", ""), re.IGNORECASE)
            if m:
                phase_number = int(m.group(1))
            else:
                phase_number = int(args.milestone)
        except Exception:
            phase_number = int(args.milestone)

        # --- STATE MACHINE TIER 1: ACTIVE PRs ---
        if getattr(args, "claim", False) and args.assignee:
            open_prs = client._request("GET", "pulls?state=open") or []
            my_prs = [pr for pr in open_prs if pr.get("user", {}).get("login") == args.assignee]
            if my_prs:
                pr = my_prs[0]
                result = {
                    "claimed_issue": {
                        "number": pr["number"],
                        "type": "pull_request",
                        "title": pr["title"],
                        "url": pr.get("html_url"),
                        "body": pr.get("body", "Please review PR feedback and push updates.")
                    }
                }
                payload = envelope(command=f"carapace queue --milestone {args.milestone}", ok=True, result=result, next_actions=[])
                print(dump_yaml(payload))
                return 0

        # --- PREPARE DAG ---
        headers, _ = build_auth_headers(token)
        all_issues = fetch_all_issues(url, repo, headers)
        phase_issues = [i for i in all_issues if _phase_of_issue(i) == phase_number]

        for issue in phase_issues:
            try:
                deps = client._request("GET", f"issues/{issue['number']}/dependencies") or []
                issue["dependencies"] = [d["number"] for d in deps]
            except GiteaAPIError:
                issue["dependencies"] = []
                    
        graph = build_graph(phase_issues)

        # --- STATE MACHINE TIER 2: IN-PROGRESS WORK ---
        in_progress = [i for i in phase_issues if "in-progress" in [l.get("name") for l in i.get("labels", [])]]
        my_in_progress = []
        if args.assignee:
            my_in_progress = [i for i in in_progress if args.assignee in [a.get("login") for a in (i.get("assignees") or [])]]
            
        if getattr(args, "claim", False) and my_in_progress:
            ip_numbers = [i["number"] for i in my_in_progress]
            ip_scores = _calculate_priority(graph, ip_numbers)
            my_in_progress = sorted(my_in_progress, key=lambda x: (ip_scores.get(x["number"], 0), -x["number"]), reverse=True)
            
            top_issue = my_in_progress[0]
            result = {
                "claimed_issue": {
                    "number": top_issue["number"],
                    "type": "issue",
                    "title": top_issue["title"],
                    "priority_score": ip_scores.get(top_issue["number"], 0),
                    "assignees": [a["login"] for a in (top_issue.get("assignees") or [])],
                    "body": top_issue.get("body", "")
                }
            }
            payload = envelope(command=f"carapace queue --milestone {args.milestone}", ok=True, result=result, next_actions=[])
            print(dump_yaml(payload))
            return 0

        # --- STATE MACHINE TIER 3: NEW WORK FROM DAG ---
        ready_raw = scheduler.compute_ready_queue()
        ready = [i for i in ready_raw if i["number"] in [i2["number"] for i2 in phase_issues]]
        
        if args.assignee:
            filtered = []
            for i in ready:
                assignees = [a.get("login") for a in (i.get("assignees") or [])]
                if args.assignee in assignees:
                    filtered.append(i)
            ready = filtered

        if not ready:
            payload = envelope(
                command=f"carapace queue --milestone {args.milestone}",
                ok=True,
                result={"status": "empty", "message": f"No unblocked issues available for this assignee/milestone (Phase {phase_number})."},
                next_actions=[]
            )
            print(dump_yaml(payload))
            return 0

        ready_numbers = [i["number"] for i in ready]
        priority_scores = _calculate_priority(graph, ready_numbers)
        ready = sorted(ready, key=lambda x: (priority_scores.get(x["number"], 0), -x["number"]), reverse=True)

        if getattr(args, "claim", False):
            top_issue = ready[0]
            iid = top_issue["number"]
            try:
                client.add_label(iid, 7)
                client.remove_label(iid, 5)
            except Exception:
                pass 
                
            result = {
                "claimed_issue": {
                    "number": iid,
                    "type": "issue",
                    "title": top_issue["title"],
                    "priority_score": priority_scores.get(iid, 0),
                    "assignees": [a["login"] for a in (top_issue.get("assignees") or [])],
                    "body": top_issue.get("body", "")
                }
            }
        else:
            result = {
                "ready_issues": [
                    {
                        "number": i["number"], 
                        "title": i["title"], 
                        "priority_score": priority_scores.get(i["number"], 0),
                        "assignees": [a["login"] for a in (i.get("assignees") or [])]
                    } for i in ready
                ]
            }
        
        payload = envelope(command=f"carapace queue --milestone {args.milestone}", ok=True, result=result, next_actions=[])
        print(dump_yaml(payload))
        return 0
    except Exception as e:
        err_msg = traceback.format_exc()
        payload = envelope(command="carapace queue", ok=False, error={"message": str(e), "traceback": err_msg}, next_actions=[])
        print(dump_yaml(payload))
        return 1
