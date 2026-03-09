import argparse
import logging
import os
import time
import traceback
from typing import Any, Dict, List, Optional

from carapace.hateoas import dump_yaml, envelope
from carapace.cli.gt import GiteaClient
from carapace.core.queue_contract import (
    build_next_actions,
    decode_queue_member,
    encode_queue_member,
    identity_from_ref,
    issue_ref_tuple,
)
from carapace.issue_ref import IssueRef
from carapace.core.scheduler import Scheduler
from carapace.worker.pool import WorkerPool, APIKeyPool
from carapace.worker.host import HostWorker
from carapace.dag import get_active_subgraph, calculate_priority


def _default_forge_for_url(url: str) -> str:
    return "github" if "github" in (url or "").lower() else "gitea"


def _labels(issue: Dict[str, Any]) -> List[str]:
    labels: List[str] = []
    for label in issue.get("labels") or []:
        if isinstance(label, dict):
            name = label.get("name")
        else:
            name = str(label)
        if name:
            labels.append(str(name))
    return labels


def _assignees(issue: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for assignee in issue.get("assignees") or []:
        if isinstance(assignee, dict):
            login = assignee.get("login") or assignee.get("username")
            if login:
                out.append(str(login))
    return out


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _build_queue_item(
    *,
    issue: Dict[str, Any],
    node_ref: IssueRef,
    graph: Any,
    priority_score: float,
    default_forge: str,
    reasons: Optional[List[str]] = None,
    kind: str = "issue",
) -> Dict[str, Any]:
    if node_ref not in graph:
        graph.add_node(node_ref)

    upstream_refs = [ref for ref in graph.predecessors(node_ref) if isinstance(ref, IssueRef)]
    downstream_refs = [ref for ref in graph.successors(node_ref) if isinstance(ref, IssueRef)]
    upstream = [identity_from_ref(ref, default_forge=default_forge) for ref in upstream_refs]
    downstream = [identity_from_ref(ref, default_forge=default_forge) for ref in downstream_refs]
    node_identity = identity_from_ref(node_ref, default_forge=default_forge)

    base_reasons = list(reasons or [])
    if not base_reasons:
        base_reasons = ["active_subgraph", "dependencies_clear"]
    if upstream:
        base_reasons.append("has_upstream_context")
    if downstream:
        base_reasons.append("has_downstream_context")
    if any(identity.get("repo") != node_identity.get("repo") for identity in upstream + downstream):
        base_reasons.append("cross_repo_context")
    if any(identity.get("forge") != default_forge for identity in upstream + downstream):
        base_reasons.append("cross_forge_context")

    return {
        "kind": kind,
        "identity": identity_from_ref(node_ref, default_forge=default_forge),
        "title": issue.get("title", ""),
        "state": issue.get("state", "open"),
        "priority_score": float(priority_score),
        "labels": _labels(issue),
        "assignees": _assignees(issue),
        "reasons": _dedupe(base_reasons),
        "upstream": upstream,
        "downstream": downstream,
        "next_actions": build_next_actions(upstream=upstream, downstream=downstream),
        "body": issue.get("body", ""),
    }


def _build_ready_queue_items(
    *,
    ready: List[Dict[str, Any]],
    graph: Any,
    repo: str,
    default_forge: str,
    reasons: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    ready_refs = [IssueRef(repo, int(issue["number"])) for issue in ready]
    priority_scores = calculate_priority(graph, ready_refs)
    sorted_ready = sorted(
        ready,
        key=lambda issue: (
            priority_scores.get(IssueRef(repo, int(issue["number"])), 0),
            -int(issue["number"]),
        ),
        reverse=True,
    )
    queue_items = []
    for issue in sorted_ready:
        ref = IssueRef(repo, int(issue["number"]))
        queue_items.append(
            _build_queue_item(
                issue=issue,
                node_ref=ref,
                graph=graph,
                priority_score=priority_scores.get(ref, 0),
                default_forge=default_forge,
                reasons=reasons or ["active_subgraph", "needs-pr", "dependencies_clear"],
            )
        )
    return queue_items


def run(args: argparse.Namespace) -> int:
    try:
        url = args.gitea_url or os.environ.get("GITEA_URL", "http://100.73.228.90:3000")
        token = args.token or os.environ.get("GITEA_TOKEN")
        repo = args.repo or os.environ.get("GITEA_REPO", "openclaw/nisto-home")
        redis_url = args.redis_url or os.environ.get("REDIS_URL")
        poll_interval = getattr(args, "poll_interval", None) or int(os.environ.get("POLL_INTERVAL", "60"))
        policy = getattr(args, "policy", "strict")
        default_forge = _default_forge_for_url(url)

        if not token:
            print(dump_yaml(envelope(command="carapace queue", ok=False, error={"message": "Missing GITEA_TOKEN"})))
            return 1

        if getattr(args, "daemon", False):
            if not redis_url:
                print(dump_yaml(envelope(command="carapace queue --daemon", ok=False, error={"message": "Redis URL is required for daemon mode"})))
                return 1
            try:
                run_daemon(url, token, repo, redis_url, poll_interval, policy)
            except KeyboardInterrupt:
                logging.info("Exiting queue daemon...")
                return 0
            except Exception as err:
                logging.error("Queue daemon encountered an error: %s", err, exc_info=True)
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

                queue_items = []
                for raw_member, score in items:
                    parsed = decode_queue_member(raw_member)
                    if not parsed:
                        continue
                    parsed["priority_score"] = float(score)
                    queue_items.append(parsed)

                if not queue_items:
                    print(
                        dump_yaml(
                            envelope(
                                command="carapace queue --redis-url",
                                ok=True,
                                result={"status": "empty", "queue_items": [], "count": 0},
                            )
                        )
                    )
                    return 0

                result = {"queue_items": queue_items, "count": len(queue_items)}
                print(dump_yaml(envelope(command="carapace queue --redis-url", ok=True, result=result)))
                return 0
            except Exception as err:
                print(
                    dump_yaml(
                        envelope(
                            command="carapace queue --redis-url",
                            ok=False,
                            error={"message": f"Failed to read from Redis: {err}"},
                        )
                    )
                )
                return 1

        if getattr(args, "claim", False) and args.assignee:
            open_prs = client._request("GET", "pulls?state=open") or []
            my_prs = [pr for pr in open_prs if pr.get("user", {}).get("login") == args.assignee]
            if my_prs:
                pr = my_prs[0]
                pr_ref = IssueRef(repo, int(pr["number"]))
                item = _build_queue_item(
                    issue=pr,
                    node_ref=pr_ref,
                    graph=scheduler.fetch_dag(),
                    priority_score=0,
                    default_forge=default_forge,
                    reasons=["active_pull_request_assigned"],
                    kind="pull_request",
                )
                result = {"claimed": item}
                command_str = "carapace queue"
                if args.milestone:
                    command_str += f" --milestone {args.milestone}"
                payload = envelope(command=command_str, ok=True, result=result, next_actions=[])
                print(dump_yaml(payload))
                return 0

        graph = scheduler.fetch_dag()
        active_nodes = get_active_subgraph(graph)

        in_progress = [
            node for node in active_nodes if "in-progress" in [label.lower() for label in graph.nodes[node].get("labels", [])]
        ]
        my_in_progress: List[Dict[str, Any]] = []
        if args.assignee:
            for node in in_progress:
                if not isinstance(node, IssueRef) or node.repo != repo:
                    continue
                issue_data = client._request("GET", f"issues/{node.number}")
                if args.assignee in _assignees(issue_data):
                    my_in_progress.append(issue_data)

        if getattr(args, "claim", False) and my_in_progress:
            in_progress_refs = [IssueRef(repo, int(issue["number"])) for issue in my_in_progress]
            scores = calculate_priority(graph, in_progress_refs)
            my_in_progress = sorted(
                my_in_progress,
                key=lambda issue: (
                    scores.get(IssueRef(repo, int(issue["number"])), 0),
                    -int(issue["number"]),
                ),
                reverse=True,
            )
            top_issue = my_in_progress[0]
            top_ref = IssueRef(repo, int(top_issue["number"]))
            item = _build_queue_item(
                issue=top_issue,
                node_ref=top_ref,
                graph=graph,
                priority_score=scores.get(top_ref, 0),
                default_forge=default_forge,
                reasons=["already_in_progress", "assignee_match"],
            )
            result = {"claimed": item}
            command_str = "carapace queue"
            if args.milestone:
                command_str += f" --milestone {args.milestone}"
            payload = envelope(command=command_str, ok=True, result=result, next_actions=[])
            print(dump_yaml(payload))
            return 0

        ready = scheduler.compute_ready_queue(policy=policy, graph=graph)
        if args.assignee:
            ready = [issue for issue in ready if args.assignee in _assignees(issue)]

        command_str = "carapace queue"
        if args.milestone:
            command_str += f" --milestone {args.milestone}"
        if not ready:
            payload = envelope(
                command=command_str,
                ok=True,
                result={"status": "empty", "queue_items": [], "count": 0},
                next_actions=[],
            )
            print(dump_yaml(payload))
            return 0

        queue_items = _build_ready_queue_items(
            ready=ready,
            graph=graph,
            repo=repo,
            default_forge=default_forge,
            reasons=["active_subgraph" if policy == "strict" else "open_issue", "needs-pr", "dependencies_clear"],
        )

        if getattr(args, "claim", False):
            top_item = queue_items[0]
            identity = top_item.get("identity", {})
            issue_number = int(identity["number"])
            try:
                client.add_label(issue_number, 7)
                client.remove_label(issue_number, 5)
            except Exception:
                pass
            result = {"claimed": top_item}
        else:
            result = {"queue_items": queue_items, "count": len(queue_items)}

        payload = envelope(command=command_str, ok=True, result=result, next_actions=[])
        print(dump_yaml(payload))
        return 0
    except Exception as err:
        payload = envelope(
            command="carapace queue",
            ok=False,
            error={"message": str(err), "traceback": traceback.format_exc()},
            next_actions=[],
        )
        print(dump_yaml(payload))
        return 1


def run_daemon(gitea_url: str, token: str, repo: str, redis_url: str, poll_interval: int, policy: str = "strict") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    client = GiteaClient(gitea_url, token, repo)
    worker_pool = WorkerPool(HostWorker(), APIKeyPool([]), max_parallel=1)
    scheduler = Scheduler(client, worker_pool, milestone=None)
    default_forge = _default_forge_for_url(gitea_url)

    import redis

    r = redis.from_url(redis_url, decode_responses=True)
    queue_key = f"carapace:queue:{repo}"

    logging.info("Starting queue daemon for %s", repo)
    logging.info("Redis URL: %s", redis_url)
    logging.info("Gitea URL: %s", gitea_url)
    logging.info("Poll interval: %ss", poll_interval)
    logging.info("Policy: %s", policy)

    while True:
        try:
            graph = scheduler.fetch_dag()
            ready_issues = scheduler.compute_ready_queue(policy=policy, graph=graph)

            if not ready_issues:
                logging.info("Ready queue is empty.")
                r.delete(queue_key)
            else:
                queue_items = _build_ready_queue_items(
                    ready=ready_issues,
                    graph=graph,
                    repo=repo,
                    default_forge=default_forge,
                    reasons=["active_subgraph" if policy == "strict" else "open_issue", "needs-pr", "dependencies_clear"],
                )
                zadd_args: Dict[str, float] = {}
                count = len(queue_items)
                for idx, item in enumerate(queue_items):
                    score = float(count - idx)
                    zadd_args[encode_queue_member(item)] = score

                pipe = r.pipeline()
                pipe.delete(queue_key)
                if zadd_args:
                    pipe.zadd(queue_key, zadd_args)
                pipe.execute()

                issue_list = []
                for item in queue_items:
                    ref_tuple = issue_ref_tuple(item, default_forge=default_forge)
                    if ref_tuple:
                        forge, item_repo, number = ref_tuple
                        issue_list.append(f"{forge}:{item_repo}#{number}")
                logging.info("Updated Redis zset '%s' with items: %s", queue_key, issue_list)
        except Exception as err:
            logging.error("Error during queue daemon update: %s", err, exc_info=True)
        time.sleep(poll_interval)
