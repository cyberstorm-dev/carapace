import argparse
import logging
import os
import re
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

import networkx as nx

from carapace.hateoas import envelope, dump_yaml
from carapace.cli.gt import GiteaClient, GiteaAPIError
from carapace.issue_ref import IssueRef
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
        redis_url = args.redis_url or os.environ.get("REDIS_URL")
        poll_interval = getattr(args, "poll_interval", None) or int(os.environ.get("POLL_INTERVAL", "60"))

        if not token:
            print(dump_yaml(envelope(command="carapace queue", ok=False, error={"message": "Missing GITEA_TOKEN"})))
            return 1

        if getattr(args, "daemon", False):
            if not redis_url:
                print(dump_yaml(envelope(
                    command="carapace queue",
                    ok=False,
                    error={"message": "Missing REDIS_URL for daemon mode"},
                )))
                return 1

            try:
                run_daemon(url, token, repo, redis_url, poll_interval)
            except KeyboardInterrupt:
                logging.info("Exiting queue daemon...")
                return 0
            except Exception as e:
                logging.error("Queue daemon encountered an error: %s", e, exc_info=True)
                return 1
            return 0

        client = GiteaClient(url, token, repo)
        dummy_pool = WorkerPool(HostWorker(), APIKeyPool([]), max_parallel=1)
        scheduler = Scheduler(client, dummy_pool)

        if redis_url:
            import redis
            try:
                r = redis.from_url(redis_url, decode_responses=True)
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
                if not isinstance(n, IssueRef) or n.repo != repo:
                    continue
                issue_data = client._request("GET", f"issues/{n.number}")
                assignees = [a.get("login") for a in (issue_data.get("assignees") or [])]
                if args.assignee in assignees:
                    my_in_progress.append(issue_data)

        if getattr(args, "claim", False) and my_in_progress:
            ip_refs = [IssueRef(repo, int(i["number"])) for i in my_in_progress]
            ip_scores = calculate_priority(graph, ip_refs)
            my_in_progress = sorted(
                my_in_progress,
                key=lambda x: (ip_scores.get(IssueRef(repo, int(x["number"])), 0), -x["number"]),
                reverse=True,
            )

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
        ready_refs = [IssueRef(repo, int(i["number"])) for i in ready]
        priority_scores = calculate_priority(graph, ready_refs)
        ready = sorted(
            ready,
            key=lambda x: (priority_scores.get(IssueRef(repo, int(x["number"])), 0), -x["number"]),
            reverse=True,
        )

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


def run_daemon(gitea_url: str, token: str, repo: str, redis_url: str, poll_interval: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    client = GiteaClient(gitea_url, token, repo)
    worker_pool = WorkerPool(HostWorker(), APIKeyPool([]), max_parallel=1)
    scheduler = Scheduler(client, worker_pool, milestone=None)

    import redis
    r = redis.from_url(redis_url, decode_responses=True)
    queue_key = f"carapace:queue:{repo}"

    logging.info("Starting queue daemon for %s", repo)
    logging.info("Redis URL: %s", redis_url)
    logging.info("Gitea URL: %s", gitea_url)
    logging.info("Poll interval: %ss", poll_interval)

    while True:
        try:
            ready_issues = scheduler.compute_ready_queue()

            if not ready_issues:
                logging.info("Ready queue is empty.")
                r.delete(queue_key)
            else:
                logging.info("Found %d ready issues.", len(ready_issues))

                zadd_args = {}
                count = len(ready_issues)
                for idx, issue in enumerate(ready_issues):
                    issue_num = str(issue["number"])
                    score = float(count - idx)
                    zadd_args[issue_num] = score

                pipe = r.pipeline()
                pipe.delete(queue_key)
                if zadd_args:
                    pipe.zadd(queue_key, zadd_args)
                pipe.execute()

                logging.info("Updated Redis zset '%s' with items: %s", queue_key, list(zadd_args.keys()))

        except Exception as e:
            logging.error("Error during queue daemon update: %s", e, exc_info=True)

        time.sleep(poll_interval)
