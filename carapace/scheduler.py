import os
import sys
from typing import List, Dict, Any

from carapace.gt import GiteaClient, GiteaAPIError
from carapace.worker.base import Worker, WorkerConfig
from carapace.worker.pool import APIKeyPool, WorkerPool, APIKey
from carapace.worker.host import HostWorker
from carapace.validator.validation import build_graph
from carapace.dag import get_active_subgraph, calculate_priority

class Scheduler:
    def __init__(self, client: GiteaClient, worker_pool: WorkerPool, milestone: str = None):
        self.client = client
        self.pool = worker_pool
        self.milestone = milestone

    def fetch_dag(self) -> Any:
        """Fetches all open issues and builds the global DiGraph."""
        issues = self.client.list_issues(state="open")
        for issue in issues:
            try:
                deps = self.client._request("GET", f"issues/{issue['number']}/dependencies") or []
                issue["dependencies"] = [d["number"] for d in deps]
            except GiteaAPIError:
                issue["dependencies"] = []
        return build_graph(issues)

    def compute_ready_queue(self) -> List[Dict[str, Any]]:
        """
        Finds issues that are open, have 'needs-pr', and are part of the active topological subgraph.
        """
        graph = self.fetch_dag()
        active_nodes = get_active_subgraph(graph)
        
        if not active_nodes:
            # Fallback for now: if no active subgraph, return nothing
            return []

        ready_issues = []
        for node in active_nodes:
            data = graph.nodes[node]
            if "needs-pr" not in [l.lower() for l in data.get("labels", [])]:
                continue
                
            # Check dependencies
            is_ready = True
            for dep in graph.predecessors(node):
                dep_data = graph.nodes[dep]
                # If dependency is in graph and NOT synthetic, it's an open issue.
                if not dep_data.get("synthetic", False):
                    # Tans are source markers; they don't block their descendants.
                    dep_labels = [l.lower() for l in dep_data.get("labels", [])]
                    if "tan" in dep_labels:
                        continue
                    is_ready = False
                    break
            
            if is_ready:
                # Re-fetch full issue data for the result
                ready_issues.append(self.client._request("GET", f"issues/{node}"))
                
        # Sort by priority (descendants in the active subgraph)
        priority_scores = calculate_priority(graph, [i["number"] for i in ready_issues])
        ready_issues = sorted(ready_issues, key=lambda x: (priority_scores.get(x["number"], 0), -x["number"]), reverse=True)
        
        return ready_issues

    def auto_merge_approved_prs(self):
        """
        Implements #116 (Merge Authority). Finds approved PRs with passing CI and merges them.
        """
        print("Checking for approved PRs to merge...")
        prs = self.client.list_issues(state="open")
        for pr in prs:
            if "pull_request" not in pr:
                continue
                
            pr_num = pr['number']
            try:
                # 1. Check reviews
                reviews = self.client._request("GET", f"pulls/{pr_num}/reviews") or []
                is_approved = any(r.get('state') == 'APPROVED' for r in reviews)
                changes_requested = any(r.get('state') == 'REQUEST_CHANGES' for r in reviews)
                
                if not is_approved or changes_requested:
                    continue

                # 2. Check CI Status (Safety Gate)
                pr_details = self.client._request("GET", f"pulls/{pr_num}")
                head_sha = pr_details.get("head", {}).get("sha")
                if not head_sha:
                    continue
                    
                status_res = self.client._request("GET", f"commits/{head_sha}/status")
                if status_res and status_res.get("state") == "success":
                    print(f"Auto-merging approved and passing PR #{pr_num}...")
                    self.client._request("POST", f"pulls/{pr_num}/merge", {"Do": "merge"})
                    print(f"✅ Merged PR #{pr_num}")
                else:
                    print(f"PR #{pr_num} is approved but CI is not 'success' (current: {status_res.get('state') if status_res else 'unknown'})")
                    
            except GiteaAPIError as e:
                if e.code == 404:
                    continue  # Normal for issues that aren't actually PRs
                print(f"Warning: Failed to fetch data for PR #{pr_num}: {e.message}")
                continue

    def run_cycle(self):
        print(f"--- Starting Scheduler Cycle for Milestone {self.milestone} ---")
        self.auto_merge_approved_prs()
        
        ready_queue = self.compute_ready_queue()
        if not ready_queue:
            print("Ready queue is empty. Nothing to dispatch.")
            return

        print(f"Found {len(ready_queue)} ready issues: {[i['number'] for i in ready_queue]}")
        
        issue_ids = [i['number'] for i in ready_queue]
        limit = self.pool.max_parallel
        targets = issue_ids[:limit]
        
        # Mark as in-progress (7) and remove needs-pr (5) before dispatching
        for iid in targets:
            try:
                print(f"Marking #{iid} as in-progress...")
                self.client.add_label(iid, 7) # in-progress
                self.client.remove_label(iid, 5) # needs-pr
            except GiteaAPIError as e:
                print(f"Warning: Failed to update labels for #{iid}: {e.message}")

        # Dispatch to the worker pool
        results = self.pool.dispatch(targets, repo=self.client.repo_full_name)
        
        for issue_id, result in zip(targets, results):
            status = "Success" if result.ok else "Failed"
            print(f"Issue #{issue_id}: {status} - {result.output}")
            if result.ok:
                # If successfully dispatched and worked on, it should have a PR now.
                # The agent inside the container is responsible for creating the PR.
                # We could remove 'in-progress' here or wait for PR to be merged.
                pass
            else:
                # If failed, maybe revert labels?
                print(f"Reverting labels for failed issue #{issue_id}")
                try:
                    self.client.add_label(issue_id, 5) # needs-pr
                    self.client.remove_label(issue_id, 7) # in-progress
                except GiteaAPIError:
                    pass


if __name__ == "__main__":
    url = os.environ.get("GITEA_URL", "http://100.73.228.90:3000")
    token = os.environ.get("GITEA_TOKEN")
    repo = os.environ.get("GITEA_REPO", "openclaw/nisto-home")
    
    if not token:
        print("GITEA_TOKEN is required.")
        sys.exit(1)
        
    client = GiteaClient(url, token, repo)
    
    # Example key pool with a single default key for now
    keys = [APIKey(label="default-key", gemini_key="dummy", gitea_token=token, model="gpt-4o")]
    key_pool = APIKeyPool(keys)
    
    # Using HostWorker temporarily to prove orchestration flow
    worker = HostWorker()
    worker_pool = WorkerPool(worker, key_pool, max_parallel=3)
    
    scheduler = Scheduler(client, worker_pool, milestone="3")
    scheduler.run_cycle()
