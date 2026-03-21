import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Union
from urllib import error, request

from carapace.issue_ref import IssueRef, parse_dependency_refs, parse_issue_ref
from carapace.hateoas import envelope, dump_yaml

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

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        repo: Optional[str] = None,
    ) -> Any:
        repo_full_name = repo or self.repo_full_name
        url = f"{self.url}/api/v1/repos/{repo_full_name}/{path}"
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

    def _web_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        *,
        accept: str = "application/json",
    ) -> Any:
        url = f"{self.url}/{self.repo_full_name}/{path.lstrip('/')}"
        headers = {
            "Accept": accept,
            "Content-Type": "application/json",
        }

        web_cookie = os.environ.get("GITEA_WEB_COOKIE")
        web_csrf = os.environ.get("GITEA_WEB_CSRF_TOKEN")

        if not web_cookie:
            raise RuntimeError("GITEA_WEB_COOKIE required for project board web operations")
        if not web_csrf:
            raise RuntimeError("GITEA_WEB_CSRF_TOKEN required for project board web operations")

        headers["Cookie"] = web_cookie
        headers["X-CSRF-Token"] = web_csrf

        req_data = json.dumps(data).encode("utf-8") if data else None
        req = request.Request(url, data=req_data, headers=headers, method=method)
        try:
            with request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                if "application/json" in (resp.headers.get("Content-Type") or ""):
                    return json.loads(raw)
                return raw
        except error.HTTPError as e:
            body = e.read().decode("utf-8")
            raise GiteaAPIError(
                message=f"Web request failed for {path}: {body[:300]}",
                code=e.code,
                reason=e.reason,
            )

    @staticmethod
    def _column_key(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())

    def _issue_internal_id(self, issue_index: int) -> int:
        issue = self.get_issue(issue_index, repo=self.repo_full_name)
        issue_id = issue.get("id")
        if not isinstance(issue_id, int):
            raise RuntimeError(f"Issue #{issue_index} has invalid internal id: {issue_id!r}")
        return issue_id

    def list_project_columns(self, project_id: int) -> List[Dict[str, Any]]:
        html = self._web_request("GET", f"projects/{project_id}", accept="text/html")
        if not isinstance(html, str):
            raise RuntimeError("Unexpected non-HTML response while reading project board")

        columns: Dict[int, Dict[str, Any]] = {}
        for col_id in re.findall(r'class="[^"]*project-column[^"]*"[^>]*data-id="(\d+)"', html):
            cid = int(col_id)
            columns[cid] = {"id": cid, "title": None, "key": None}

        titles = re.findall(r'class="project-column-title-text"[^>]*>([^<]+)<', html)
        ids_sorted = sorted(columns.keys())
        for idx, title in enumerate(titles):
            if idx >= len(ids_sorted):
                break
            cid = ids_sorted[idx]
            title_clean = title.strip()
            columns[cid]["title"] = title_clean
            columns[cid]["key"] = self._column_key(title_clean)

        resolved = [c for c in columns.values() if c["title"]]
        resolved.sort(key=lambda c: c["id"])
        return resolved

    def move_issue_to_project_column(
        self, project_id: int, issue_index: int, target_column: str
    ) -> Dict[str, Any]:
        columns = self.list_project_columns(project_id)
        if not columns:
            raise RuntimeError(f"No columns found for project #{project_id}")

        target_key = self._column_key(target_column)
        target = next(
            (
                c
                for c in columns
                if c["key"] == target_key or c["title"].lower() == target_column.lower()
            ),
            None,
        )
        if not target:
            known = ", ".join(c["title"] for c in columns)
            raise RuntimeError(
                f"Column '{target_column}' not found in project #{project_id}. Known: {known}"
            )

        issue_id = self._issue_internal_id(issue_index)
        payload = {"issues": [{"issueID": issue_id, "sorting": 0}]}
        self._web_request("POST", f"projects/{project_id}/{target['id']}/move", payload)
        return {
            "issue_number": issue_index,
            "issue_id": issue_id,
            "project_id": project_id,
            "column_id": target["id"],
            "column_title": target["title"],
        }

    def list_issues(
        self,
        state: str = "open",
        assignee: Optional[str] = None,
        labels: Optional[str] = None,
        milestone: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params = [f"state={state}", "limit=100"]
        if assignee:
            params.append(f"assignee={assignee}")
        if labels:
            params.append(f"labels={labels}")
        if milestone:
            params.append(f"milestone={milestone}")
        
        path = f"issues?{'&'.join(params)}"
        issues = self._request("GET", path, repo=repo)
        if assignee is None:
            return issues

        def _assignee_login(issue: Dict[str, Any]) -> Optional[str]:
            issue_assignee = issue.get("assignee")
            if isinstance(issue_assignee, dict):
                return issue_assignee.get("login")
            if isinstance(issue_assignee, str):
                return issue_assignee
            return None

        return [
            issue
            for issue in issues
            if _assignee_login(issue) == assignee
        ]

    def add_dependency(self, issue_index: int, dep_reference: Union[int, str, IssueRef]):
        dependency = parse_issue_ref(dep_reference, default_repo=self.repo_full_name)
        if dependency is None:
            raise ValueError("Unable to parse dependency reference")
        # Check if already exists to prevent Gitea 500/duplicate errors
        deps = self.list_dependencies(issue_index, repo=self.repo_full_name)
        if dependency in deps:
            return {"message": "Dependency already exists"}

        owner, repo = dependency.repo.split("/", 1)
        payload = {
            "index": dependency.number,
            "owner": owner,
            "repo": repo,
        }
        return self._request("POST", f"issues/{issue_index}/dependencies", payload, repo=self.repo_full_name)

    def remove_dependency(self, issue_index: int, dep_reference: Union[int, str, IssueRef]):
        dependency = parse_issue_ref(dep_reference, default_repo=self.repo_full_name)
        if dependency is None:
            raise ValueError("Unable to parse dependency reference")
        # DELETE /dependencies requires the IssueMeta in the body
        deps = self.list_dependencies(issue_index, repo=self.repo_full_name)
        if dependency not in deps:
            raise RuntimeError(f"Dependency #{dependency.number} not found on issue #{issue_index}")

        owner, repo = dependency.repo.split("/", 1)
        payload = {
            "index": dependency.number,
            "owner": owner,
            "repo": repo,
        }
        return self._request("DELETE", f"issues/{issue_index}/dependencies", payload, repo=self.repo_full_name)

    def list_dependencies(self, issue_index: int, repo: Optional[str] = None) -> List[IssueRef]:
        repo_full_name = repo or self.repo_full_name
        deps = self._request("GET", f"issues/{issue_index}/dependencies", repo=repo_full_name) or []
        return parse_dependency_refs(deps, default_repo=repo_full_name)

    def get_issue(self, issue_index: int, repo: Optional[str] = None) -> Dict[str, Any]:
        return self._request("GET", f"issues/{issue_index}", repo=repo or self.repo_full_name)

    def add_label(self, issue_index: int, label_id: int):
        # Gitea POST to /labels adds to the existing set
        payload = {"labels": [label_id]}
        return self._request("POST", f"issues/{issue_index}/labels", payload)

    def remove_label(self, issue_index: int, label_id: int):
        # Gitea DELETE to /labels/{id} removes that specific label
        return self._request("DELETE", f"issues/{issue_index}/labels/{label_id}")

    def patch_issue(self, issue_index: int, data: Dict[str, Any]):
        """Updates issue metadata via PATCH."""
        return self._request("PATCH", f"issues/{issue_index}", data)

    def get_labels(self) -> List[Dict[str, Any]]:
        """Fetches all repository labels."""
        return self._request("GET", "labels") or []

    def list_comments(self, issue_index: int) -> List[Dict[str, Any]]:
        return self._request("GET", f"issues/{issue_index}/comments") or []

    def create_comment(self, issue_index: int, body: str) -> Dict[str, Any]:
        return self._request("POST", f"issues/{issue_index}/comments", {"body": body})

    def update_comment(self, comment_id: int, body: str) -> Dict[str, Any]:
        # Repository-scoped comment edit route in Gitea API
        return self._request("PATCH", f"issues/comments/{comment_id}", {"body": body})

    def find_workpad_comment(
        self, issue_index: int, marker: str = "## Codex Workpad"
    ) -> Optional[Dict[str, Any]]:
        comments = self.list_comments(issue_index)
        for c in comments:
            body = c.get("body")
            if isinstance(body, str) and marker in body:
                return c
        return None

    def upsert_workpad_comment(
        self,
        issue_index: int,
        body: str,
        marker: str = "## Codex Workpad",
    ) -> Dict[str, Any]:
        if marker not in body:
            raise ValueError(f"workpad body must contain marker: {marker!r}")
        existing = self.find_workpad_comment(issue_index, marker=marker)
        if existing:
            cid = existing.get("id")
            if not isinstance(cid, int):
                raise RuntimeError(f"Existing workpad has invalid comment id: {cid!r}")
            updated = self.update_comment(cid, body)
            updated["_workpad_action"] = "updated"
            return updated
        created = self.create_comment(issue_index, body)
        created["_workpad_action"] = "created"
        return created


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
    dep_parser.add_argument("dependency")

    # Issue labels
    label_parser = subparsers.add_parser("label", help="Manage issue labels")
    label_parser.add_argument("action", choices=["add", "rm"])
    label_parser.add_argument("issue", type=int)
    label_parser.add_argument("label_id", type=int)

    # Issue comments
    comment_parser = subparsers.add_parser("comment", help="Issue comment operations")
    comment_subparsers = comment_parser.add_subparsers(dest="comment_action")

    comment_list = comment_subparsers.add_parser("list", help="List comments on an issue")
    comment_list.add_argument("issue", type=int)

    comment_add = comment_subparsers.add_parser("add", help="Create a comment on an issue")
    comment_add.add_argument("issue", type=int)
    comment_add_group = comment_add.add_mutually_exclusive_group(required=True)
    comment_add_group.add_argument("--body", help="Comment body text")
    comment_add_group.add_argument("--body-file", help="Path to file containing comment body")

    # Workpad (single persistent issue comment)
    workpad_parser = subparsers.add_parser("workpad", help="Single-comment workpad operations")
    workpad_subparsers = workpad_parser.add_subparsers(dest="workpad_action")

    workpad_get = workpad_subparsers.add_parser("get", help="Get workpad comment by marker")
    workpad_get.add_argument("issue", type=int)
    workpad_get.add_argument("--marker", default="## Codex Workpad")

    workpad_upsert = workpad_subparsers.add_parser(
        "upsert", help="Create or update workpad comment by marker"
    )
    workpad_upsert.add_argument("issue", type=int)
    workpad_upsert.add_argument("--marker", default="## Codex Workpad")
    workpad_upsert_group = workpad_upsert.add_mutually_exclusive_group(required=True)
    workpad_upsert_group.add_argument("--body", help="Full workpad body text")
    workpad_upsert_group.add_argument("--body-file", help="Path to file containing full workpad body")

    # Project board operations (web-routed in Gitea)
    project_parser = subparsers.add_parser("project", help="Project board operations")
    project_subparsers = project_parser.add_subparsers(dest="project_action")

    project_cols = project_subparsers.add_parser("columns", help="List project columns")
    project_cols.add_argument("project_id", type=int)

    project_move = project_subparsers.add_parser(
        "move", help="Move issue card to a project column"
    )
    project_move.add_argument("project_id", type=int)
    project_move.add_argument("issue", type=int, help="Issue number (index)")
    project_move.add_argument(
        "--to", required=True, help="Target column name (e.g. 'To Do', 'In Progress')"
    )

    if len(sys.argv) == 1:
        # Self-documenting command tree
        payload = envelope(
            command="gt",
            ok=True,
            result={
                "description": "Gitea Tool for Agentic Workflows",
                "commands": [
                    {"name": "list", "description": "List issues with filtering", "usage": "gt list [--state open|closed|all] [--assignee user] [--labels l1,l2]"},
                    {
                        "name": "dep add",
                        "description": "Add dependency to issue",
                        "usage": "gt dep add <issue_index> <dep_reference>",
                    },
                    {
                        "name": "dep rm",
                        "description": "Remove dependency from issue",
                        "usage": "gt dep rm <issue_index> <dep_reference>",
                    },
                    {"name": "label add", "description": "Add label to issue", "usage": "gt label add <issue_index> <label_id>"},
                    {"name": "label rm", "description": "Remove label from issue", "usage": "gt label rm <issue_index> <label_id>"},
                    {"name": "project columns", "description": "List columns for a project board", "usage": "gt project columns <project_id>"},
                    {"name": "project move", "description": "Move issue card to a board column", "usage": "gt project move <project_id> <issue_number> --to \"In Progress\""},
                    {"name": "comment list", "description": "List issue comments", "usage": "gt comment list <issue_index>"},
                    {"name": "comment add", "description": "Add issue comment", "usage": "gt comment add <issue_index> --body-file workpad.md"},
                    {"name": "workpad get", "description": "Get single workpad comment", "usage": "gt workpad get <issue_index> [--marker \"## Codex Workpad\"]"},
                    {"name": "workpad upsert", "description": "Create/update workpad comment", "usage": "gt workpad upsert <issue_index> --body-file workpad.md [--marker \"## Codex Workpad\"]"},
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

        elif args.command == "project":
            if args.project_action == "columns":
                columns = client.list_project_columns(args.project_id)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"project_id": args.project_id, "columns": columns},
                    next_actions=[
                        {"command": f"gt project move {args.project_id} 1 --to \"In Progress\"", "description": "Move issue #1 to In Progress"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.project_action == "move":
                moved = client.move_issue_to_project_column(
                    args.project_id, args.issue, args.to
                )
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"message": "Issue moved on project board", **moved},
                    next_actions=[
                        {"command": f"gt project columns {args.project_id}", "description": "List project columns"},
                    ],
                )
                print(dump_yaml(payload))
            else:
                parser.print_help()

        elif args.command == "comment":
            if args.comment_action == "list":
                comments = client.list_comments(args.issue)
                result = [
                    {
                        "id": c.get("id"),
                        "user": (c.get("user") or {}).get("login"),
                        "created_at": c.get("created_at"),
                        "updated_at": c.get("updated_at"),
                        "body": c.get("body"),
                    }
                    for c in comments
                ]
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"issue": args.issue, "count": len(result), "comments": result},
                )
                print(dump_yaml(payload))
            elif args.comment_action == "add":
                body = args.body
                if args.body_file:
                    with open(args.body_file, "r", encoding="utf-8") as f:
                        body = f.read()
                created = client.create_comment(args.issue, body or "")
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "issue": args.issue,
                        "comment_id": created.get("id"),
                        "html_url": created.get("html_url"),
                    },
                )
                print(dump_yaml(payload))
            else:
                parser.print_help()

        elif args.command == "workpad":
            if args.workpad_action == "get":
                workpad = client.find_workpad_comment(args.issue, marker=args.marker)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "issue": args.issue,
                        "marker": args.marker,
                        "exists": workpad is not None,
                        "comment": workpad,
                    },
                )
                print(dump_yaml(payload))
            elif args.workpad_action == "upsert":
                body = args.body
                if args.body_file:
                    with open(args.body_file, "r", encoding="utf-8") as f:
                        body = f.read()
                result = client.upsert_workpad_comment(
                    args.issue, body or "", marker=args.marker
                )
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "issue": args.issue,
                        "marker": args.marker,
                        "action": result.get("_workpad_action"),
                        "comment_id": result.get("id"),
                        "html_url": result.get("html_url"),
                    },
                )
                print(dump_yaml(payload))
            else:
                parser.print_help()

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
