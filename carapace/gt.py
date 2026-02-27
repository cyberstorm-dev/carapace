import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional
from urllib import error, request

from .hateoas import envelope, dump_yaml

DEFAULT_GITEA_URL = "http://100.73.228.90:3000"


class GiteaAPIError(Exception):
    def __init__(self, message, code, reason):
        super().__init__(message)
        self.message = message
        self.code = code
        self.reason = reason


class GiteaClient:
    def __init__(self, url: str, token: str, repo: str):
        self.url = url.rstrip("/")
        self.token = token
        self.repo_full_name = repo
        self.owner, self.repo = repo.split("/")

    def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.url}/api/v1/repos/{self.repo_full_name}/{path}"
        headers = {
            "Authorization": f"token {self.token}",
            "Content-Type": "application/json",
        }
        req_data = json.dumps(data).encode("utf-8") if data else None
        req = request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with request.urlopen(req) as resp:
                if resp.status == 204:
                    return None
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                error_data = json.loads(body)
            except json.JSONDecodeError:
                error_data = {"message": body}
            
            raise GiteaAPIError(
                message=error_data.get("message", "Unknown error"),
                code=e.code,
                reason=e.reason
            )
        except Exception as e:
             raise

    def list_issues(self, state: str = "open", assignee: Optional[str] = None, labels: Optional[str] = None, milestone: Optional[str] = None) -> List[Dict[str, Any]]:
        params = [f"state={state}"]
        if assignee:
            params.append(f"assignee={assignee}")
        if labels:
            params.append(f"labels={labels}")
        if milestone:
            params.append(f"milestone={milestone}")
        
        path = f"issues?{'&'.join(params)}"
        return self._request("GET", path)

    def add_dependency(self, issue_index: int, dep_index: int):
        # Check if already exists to prevent Gitea 500/duplicate errors
        deps = self._request("GET", f"issues/{issue_index}/dependencies")
        if any(d["number"] == dep_index for d in deps):
            return {"message": "Dependency already exists"}

        payload = {
            "index": dep_index,
            "owner": self.owner,
            "repo": self.repo,
        }
        return self._request("POST", f"issues/{issue_index}/dependencies", payload)

    def remove_dependency(self, issue_index: int, dep_index: int):
        # DELETE /dependencies requires the IssueMeta in the body
        deps = self._request("GET", f"issues/{issue_index}/dependencies")
        if not any(d["number"] == dep_index for d in deps):
            raise RuntimeError(f"Dependency #{dep_index} not found on issue #{issue_index}")
        
        payload = {
            "index": dep_index,
            "owner": self.owner,
            "repo": self.repo,
        }
        return self._request("DELETE", f"issues/{issue_index}/dependencies", payload)

    def add_label(self, issue_index: int, label_id: int):
        # Gitea POST to /labels adds to the existing set
        payload = {"labels": [label_id]}
        return self._request("POST", f"issues/{issue_index}/labels", payload)

    def remove_label(self, issue_index: int, label_id: int):
        # Gitea DELETE to /labels/{id} removes that specific label
        return self._request("DELETE", f"issues/{issue_index}/labels/{label_id}")


def main():
    parser = argparse.ArgumentParser(description="gt: Gitea Tool for Agentic Workflows", add_help=False)
    parser.add_argument("--url", default=os.environ.get("GITEA_URL", DEFAULT_GITEA_URL))
    parser.add_argument("--token", default=os.environ.get("GITEA_TOKEN"))
    parser.add_argument("--repo", default=os.environ.get("GITEA_REPO", "openclaw/nisto-home"))

    subparsers = parser.add_subparsers(dest="command")

    # Issue listing
    list_parser = subparsers.add_parser("list", help="List issues with filters")
    list_parser.add_argument("--state", default="open", choices=["open", "closed", "all"])
    list_parser.add_argument("--assignee", help="Filter by assignee username")
    list_parser.add_argument("--labels", help="Comma-separated label names")
    list_parser.add_argument("--milestone", help="Filter by milestone title or id")

    # Issue dependencies
    dep_parser = subparsers.add_parser("dep", help="Manage issue dependencies")
    dep_parser.add_argument("action", choices=["add", "rm"])
    dep_parser.add_argument("issue", type=int)
    dep_parser.add_argument("dependency", type=int)

    # Issue labels
    label_parser = subparsers.add_parser("label", help="Manage issue labels")
    label_parser.add_argument("action", choices=["add"])
    label_parser.add_argument("issue", type=int)
    label_parser.add_argument("label_id", type=int)

    if len(sys.argv) == 1:
        # Self-documenting command tree
        payload = envelope(
            command="gt",
            ok=True,
            result={
                "description": "Gitea Tool for Agentic Workflows",
                "commands": [
                    {"name": "list", "description": "List issues with filtering", "usage": "gt list [--state open|closed|all] [--assignee user] [--labels l1,l2]"},
                    {"name": "dep add", "description": "Add dependency to issue", "usage": "gt dep add <issue_index> <dep_index>"},
                    {"name": "dep rm", "description": "Remove dependency from issue", "usage": "gt dep rm <issue_index> <dep_index>"},
                    {"name": "label add", "description": "Add label to issue", "usage": "gt label add <issue_index> <label_id>"},
                ]
            },
            next_actions=[
                {"command": "gt list", "description": "List all open issues"},
            ]
        )
        print(dump_yaml(payload))
        return

    args = parser.parse_args()

    if not args.token:
        payload = envelope(
            command="gt",
            ok=False,
            error={"message": "GITEA_TOKEN required"},
            fix="Set GITEA_TOKEN environment variable or use --token flag.",
        )
        print(dump_yaml(payload))
        sys.exit(1)

    client = GiteaClient(args.url, args.token, args.repo)
    full_cmd = " ".join(sys.argv)

    try:
        if args.command == "list":
            issues = client.list_issues(state=args.state, assignee=args.assignee, labels=args.labels, milestone=args.milestone)
            result_issues = [
                {
                    "number": i["number"],
                    "state": i["state"],
                    "title": i["title"],
                    "assignee": i.get("assignee").get("login") if i.get("assignee") else None
                }
                for i in issues
            ]
            payload = envelope(
                command=full_cmd,
                ok=True,
                result={"issues": result_issues, "count": len(result_issues)},
                next_actions=[
                    {"command": f"gt list --state closed", "description": "List closed issues"},
                ]
            )
            print(dump_yaml(payload))

        elif args.command == "dep":
            if args.action == "add":
                client.add_dependency(args.issue, args.dependency)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"message": f"Added dependency: #{args.issue} depends on #{args.dependency}"},
                    next_actions=[
                        {"command": f"gt dep rm {args.issue} {args.dependency}", "description": "Remove this dependency"},
                    ]
                )
                print(dump_yaml(payload))
            elif args.action == "rm":
                client.remove_dependency(args.issue, args.dependency)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"message": f"Removed dependency: #{args.issue} from #{args.dependency}"},
                    next_actions=[
                        {"command": f"gt dep add {args.issue} {args.dependency}", "description": "Re-add this dependency"},
                    ]
                )
                print(dump_yaml(payload))

        elif args.command == "label":
            if args.action == "add":
                client.add_label(args.issue, args.label_id)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"message": f"Added label {args.label_id} to issue #{args.issue}"},
                    next_actions=[
                        {"command": f"gt list --labels {args.label_id}", "description": "List other issues with this label"},
                    ]
                )
                print(dump_yaml(payload))
            elif args.action == "rm":
                client.remove_label(args.issue, args.label_id)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"message": f"Removed label {args.label_id} from issue #{args.issue}"},
                    next_actions=[
                        {"command": f"gt label add {args.issue} {args.label_id}", "description": "Re-add this label"},
                    ]
                )
                print(dump_yaml(payload))

        else:
            parser.print_help()
    except GiteaAPIError as e:
        payload = envelope(
            command=full_cmd,
            ok=False,
            error={
                "message": e.message,
                "code": e.code,
                "reason": e.reason,
            },
            fix="Verify your GITEA_TOKEN and issue/dependency indices. Ensure you're not adding a duplicate dependency.",
            next_actions=[
                {"command": "gt list", "description": "List current issues"},
            ]
        )
        print(dump_yaml(payload))
        sys.exit(1)
    except Exception as e:
        payload = envelope(
            command=full_cmd,
            ok=False,
            error={"message": str(e), "type": type(e).__name__},
            fix="Check network connectivity and Gitea URL.",
        )
        print(dump_yaml(payload))
        sys.exit(1)


if __name__ == "__main__":
    main()
