import argparse
from typing import Dict, Any, List, Optional
import os
import traceback
import sys
import re

import networkx as nx

from carapace.hateoas import envelope, dump_yaml
from carapace.cli.gt import GiteaClient, GiteaAPIError
from carapace.core.scheduler import Scheduler
from carapace.worker.pool import WorkerPool, APIKeyPool
from carapace.worker.host import HostWorker
from carapace.dag import get_active_subgraph, calculate_priority
from carapace.validator.cli import build_auth_headers, fetch_all_issues

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
        scheduler = Scheduler(client, dummy_pool)
        
        if getattr(args, "redis_url", None):
            import redis
            try:
                r = redis.from_url(args.redis_url, decode_responses=True)
                queue_key = f"carapace:queue:{repo}"
                items = r.zrevrange(queue_key, 0, -1, withscores=True)
                
                if not items:
                    print(dump_yaml(envelope(
                        command="carapace queue --redis-url",
                        ok=True,
                        result={"status": "empty", "message": "Redis queue is empty."}
                    )))
                    return 0

                ready_issues = []
                for issue_id_str, score in items:
                    issue_id = int(issue_id_str)
                    try:
                        issue_data = client._request("GET", f"issues/{issue_id}")
                        ready_issues.append({
                            "number": issue_data["number"],
                            "title": issue_data["title"],
                            "priority_score": score,
                            "assignees": [a["login"] for a in (issue_data.get("assignees") or [])]
                        })
                    except Exception:
                        ready_issues.append({
                            "number": issue_id,
                            "title": "Unknown (failed to fetch)",
                            "priority_score": score,
                            "assignees": []
                        })
                
                result = {"ready_issues": ready_issues}
                print(dump_yaml(envelope(command="carapace queue --redis-url", ok=True, result=result)))
                return 0
                
            except Exception as e:
                print(dump_yaml(envelope(
                    command="carapace queue --redis-url",
                    ok=False,
                    error={"message": f"Failed to read from Redis: {e}"}
                )))
                return 1

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
                command_str = "carapace queue"
                if args.milestone:
                    command_str += f" --milestone {args.milestone}"
                payload = envelope(command=command_str, ok=True, result=result, next_actions=[])
                print(dump_yaml(payload))
                return 0

        # --- PREPARE DAG ---
        graph = scheduler.fetch_dag()
        active_nodes = get_active_subgraph(graph)

        # --- STATE MACHINE TIER 2: IN-PROGRESS WORK ---
        in_progress = [n for n in active_nodes if "in-progress" in [l.lower() for l in graph.nodes[n].get("labels", [])]]
        my_in_progress = []
        if args.assignee:
            for n in in_progress:
                issue_data = client._request("GET", f"issues/{n}")
                assignees = [a.get("login") for a in (issue_data.get("assignees") or [])]
                if args.assignee in assignees:
                    my_in_progress.append(issue_data)
            
        if getattr(args, "claim", False) and my_in_progress:
            ip_numbers = [i["number"] for i in my_in_progress]
            ip_scores = calculate_priority(graph, ip_numbers)
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
            command_str = "carapace queue"
            if args.milestone:
                command_str += f" --milestone {args.milestone}"
            payload = envelope(command=command_str, ok=True, result=result, next_actions=[])
            print(dump_yaml(payload))
            return 0

        # --- STATE MACHINE TIER 3: NEW WORK FROM DAG ---
        ready = scheduler.compute_ready_queue()
        
        if args.assignee:
            filtered = []
            for i in ready:
                assignees = [a.get("login") for a in (i.get("assignees") or [])]
                if args.assignee in assignees:
                    filtered.append(i)
            ready = filtered

        command_str = "carapace queue"
        if args.milestone:
            command_str += f" --milestone {args.milestone}"
        if not ready:
            payload = envelope(
                command=command_str,
                ok=True,
                result={"status": "empty", "message": f"No unblocked issues available in the active topological subgraph."},
                next_actions=[]
            )
            print(dump_yaml(payload))
            return 0

        ready_numbers = [i["number"] for i in ready]
        priority_scores = calculate_priority(graph, ready_numbers)
        ready = sorted(ready, key=lambda x: (priority_scores.get(x["number"], 0), -x["number"]), reverse=True)

        if getattr(args, "claim", False):
            top_issue = ready[0]
            iid = top_issue["number"]
            try:
                client.add_label(iid, 7) # in-progress
                client.remove_label(iid, 5) # needs-pr
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
        
        command_str = "carapace queue"
        if args.milestone:
            command_str += f" --milestone {args.milestone}"
        payload = envelope(command=command_str, ok=True, result=result, next_actions=[])
        print(dump_yaml(payload))
        return 0
    except Exception as e:
        err_msg = traceback.format_exc()
        payload = envelope(command="carapace queue", ok=False, error={"message": str(e), "traceback": err_msg}, next_actions=[])
        print(dump_yaml(payload))
        return 1
