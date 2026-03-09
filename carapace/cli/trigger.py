import os
from typing import Any, Dict, List, Optional
import argparse
import sys
import redis

from carapace.hateoas import envelope
from carapace.cli.gt import GiteaClient, GiteaAPIError
from carapace.core.queue_contract import decode_queue_member, issue_ref_tuple


def _extract_queue_items(raw_members: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for member in raw_members:
        parsed = decode_queue_member(member)
        if parsed:
            items.append(parsed)
    return items


def _extract_queue_issue_refs(raw_members: List[str], default_forge: str = "gitea") -> List[tuple[str, str, int]]:
    refs: List[tuple[str, str, int]] = []
    for parsed in _extract_queue_items(raw_members):
        ref = issue_ref_tuple(parsed, default_forge=default_forge)
        if not ref:
            continue
        refs.append(ref)
    return refs


def _format_identity(identity: Dict[str, Any], default_forge: str = "gitea") -> Optional[str]:
    if not isinstance(identity, dict):
        return None
    forge = identity.get("forge") or default_forge
    repo = identity.get("repo")
    number = identity.get("number")
    if not repo or number is None:
        return None
    try:
        number = int(number)
    except (TypeError, ValueError):
        return None
    return f"{forge}:{repo}#{number}"


def _build_queue_next_actions(
    queue_items: List[Dict[str, Any]],
    default_forge: str,
    target_repo: str,
    redis_url: Optional[str] = None,
) -> List[Dict[str, str]]:
    repo_items: List[Dict[str, Any]] = []
    for item in queue_items:
        ref = issue_ref_tuple(item, default_forge=default_forge)
        if not ref:
            continue
        forge, repo, _ = ref
        if forge == default_forge and repo == target_repo:
            repo_items.append(item)

    if not repo_items:
        return []

    top_item = repo_items[0]
    top_ref = issue_ref_tuple(top_item, default_forge=default_forge)
    if not top_ref:
        return []
    forge, repo, number = top_ref
    top_ref_text = f"{forge}:{repo}#{number}"

    queue_cmd = f"carapace queue --repo {target_repo}"
    if redis_url:
        queue_cmd = f"{queue_cmd} --redis-url {redis_url}"
    claim_cmd = f"{queue_cmd} --claim"

    actions: List[Dict[str, str]] = [
        {
            "command": claim_cmd,
            "description": f"Claim top queue item {top_ref_text}",
        }
    ]

    upstream_refs = [
        _format_identity(identity, default_forge=default_forge)
        for identity in (top_item.get("upstream") or [])
    ]
    upstream_refs = [ref for ref in upstream_refs if ref]
    if upstream_refs:
        actions.append(
            {
                "command": queue_cmd,
                "description": f"Inspect upstream blockers for {top_ref_text}: {', '.join(upstream_refs)}",
            }
        )

    downstream_refs = [
        _format_identity(identity, default_forge=default_forge)
        for identity in (top_item.get("downstream") or [])
    ]
    downstream_refs = [ref for ref in downstream_refs if ref]
    if downstream_refs:
        actions.append(
            {
                "command": queue_cmd,
                "description": f"Inspect downstream dependents for {top_ref_text}: {', '.join(downstream_refs)}",
            }
        )

    return actions


def run(args: argparse.Namespace) -> int:
    client = GiteaClient(args.gitea_url, args.token, args.repo)
    triggers = []
    default_forge = "github" if "github" in (args.gitea_url or "").lower() else "gitea"

    try:
        pulls = client._request("GET", "pulls?state=open") or []
        pr_numbers = set()
        
        for pr in pulls:
            pr_num = pr["number"]
            pr_numbers.add(pr_num)
            
            # Re-fetch PR to get mergeable status reliably
            try:
                pr_detail = client._request("GET", f"pulls/{pr_num}")
                
                # Check for conflicts
                if pr_detail.get("mergeable") is False:
                    triggers.append({
                        "agent": "builder",
                        "reason": f"PR #{pr_num} has conflicts and needs rebase/fix"
                    })
                    continue
                    
                # Check reviews
                reviews = client._request("GET", f"pulls/{pr_num}/reviews") or []
                is_approved = any(r.get("state") == "APPROVED" for r in reviews)
                changes_requested = any(r.get("state") == "REQUEST_CHANGES" for r in reviews)
                
                if changes_requested:
                    triggers.append({
                        "agent": "builder",
                        "reason": f"PR #{pr_num} has requested changes"
                    })
                    continue
                    
                if not is_approved:
                    triggers.append({
                        "agent": "reviewer",
                        "reason": f"PR #{pr_num} needs review"
                    })
                    continue
                    
                # Approved, check CI
                head_sha = pr_detail.get("head", {}).get("sha")
                if head_sha:
                    status_res = client._request("GET", f"commits/{head_sha}/status")
                    if status_res and status_res.get("state") == "success":
                        triggers.append({
                            "agent": "nisto",
                            "reason": f"PR #{pr_num} is approved and green, ready to merge"
                        })
                    elif status_res and status_res.get("state") in ("failure", "error"):
                        triggers.append({
                            "agent": "builder",
                            "reason": f"PR #{pr_num} has failing CI"
                        })
                        
            except GiteaAPIError as e:
                print(f"Error fetching details for PR #{pr_num}: {e.message}", file=sys.stderr)

        # Check for issues that need work
        # Prioritize checking the Redis queue if available
        redis_queue_items: List[tuple[str, str, int]] = []
        redis_queue_payloads: List[Dict[str, Any]] = []
        if args.redis_url:
            try:
                r = redis.from_url(args.redis_url, decode_responses=True)
                queue_key = f"carapace:queue:{args.repo}"
                raw_members = r.zrevrange(queue_key, 0, -1)
                redis_queue_payloads = _extract_queue_items(raw_members)
                redis_queue_items = [
                    ref
                    for ref in (
                        issue_ref_tuple(item, default_forge=default_forge) for item in redis_queue_payloads
                    )
                    if ref
                ]
            except Exception as e:
                print(f"Warning: Failed to connect to Redis queue: {e}", file=sys.stderr)

        repo_ready_queue = [num for forge, repo_name, num in redis_queue_items if forge == default_forge and repo_name == args.repo]
        queue_next_actions = _build_queue_next_actions(
            queue_items=redis_queue_payloads,
            default_forge=default_forge,
            target_repo=args.repo,
            redis_url=args.redis_url,
        )
        queue_head = None
        for item in redis_queue_payloads:
            ref = issue_ref_tuple(item, default_forge=default_forge)
            if not ref:
                continue
            forge, repo_name, _ = ref
            if forge == default_forge and repo_name == args.repo:
                queue_head = item
                break
        
        # Check assigned issues
        issues = client.list_issues(state="open")
        for issue in issues:
            # Skip if it's a PR
            if "pull_request" in issue:
                continue
                
            issue_num = issue["number"]
            assignees = [a.get("login") for a in issue.get("assignees", [])]
            labels = [l.get("name") for l in issue.get("labels", [])]
            
            has_pr = False
            # This is a heuristic. A robust implementation would use issue/PR cross-references or carapace pr-issue-ref
            for pr in pulls:
                body = pr.get("body") or ""
                if f"#{issue_num}" in body or str(issue_num) in body:
                    has_pr = True
                    break
            
            if not has_pr:
                if "builder" in assignees or "in-progress" in labels:
                    triggers.append({
                        "agent": "builder",
                        "reason": f"Issue #{issue_num} is active but has no open PR"
                    })
                elif issue_num in repo_ready_queue:
                    # It's at the top of the ready queue
                    if repo_ready_queue.index(issue_num) == 0:
                         triggers.append({
                            "agent": "builder",
                            "reason": f"Issue #{issue_num} is top of the ready queue"
                         })
                         
        if not triggers:
             triggers.append({
                 "agent": "none",
                 "reason": "All PRs reviewed, no active issues need attention"
             })

        result = {"triggers": triggers}
        if queue_head is not None:
            result["queue_head"] = queue_head

        payload = envelope(
            command="carapace trigger",
            ok=True,
            result=result,
            next_actions=queue_next_actions
        )
        return payload, 0
        
    except Exception as e:
        payload = envelope(
            command="carapace trigger",
            ok=False,
            error={"message": str(e), "type": type(e).__name__},
            fix="Check connection parameters"
        )
        return payload, 1
