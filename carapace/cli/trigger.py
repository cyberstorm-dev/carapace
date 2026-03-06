import os
from typing import Any, Dict, List, Optional
import argparse
import sys
import redis

from carapace.hateoas import envelope
from carapace.cli.gt import GiteaClient, GiteaAPIError


def run(args: argparse.Namespace) -> int:
    client = GiteaClient(args.gitea_url, args.token, args.repo)
    triggers = []

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
        redis_queue_items = []
        if args.redis_url:
            try:
                r = redis.from_url(args.redis_url, decode_responses=True)
                queue_key = f"carapace:queue:{args.repo}"
                redis_queue_items = r.zrevrange(queue_key, 0, -1)
            except Exception as e:
                print(f"Warning: Failed to connect to Redis queue: {e}", file=sys.stderr)
        
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
                elif str(issue_num) in redis_queue_items:
                    # It's at the top of the ready queue
                    if redis_queue_items.index(str(issue_num)) == 0:
                         triggers.append({
                            "agent": "builder",
                            "reason": f"Issue #{issue_num} is top of the ready queue"
                         })
                         
        if not triggers:
             triggers.append({
                 "agent": "none",
                 "reason": "All PRs reviewed, no active issues need attention"
             })

        payload = envelope(
            command="carapace trigger",
            ok=True,
            result={"triggers": triggers},
            next_actions=[]
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
