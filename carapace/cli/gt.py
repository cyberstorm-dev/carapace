import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Union
from urllib import error, request
from urllib.parse import urlencode

from carapace.issue_ref import IssueRef, parse_dependency_refs, parse_issue_ref
from carapace.hateoas import envelope, dump_yaml

DEFAULT_GITEA_URL = "http://100.73.228.90:3000"
DEFAULT_CONFIG_PATH = "~/.config/carapace/gt.toml"
DEFAULT_KANBAN_COLUMNS = ("Backlog", "To Do", "In Progress", "Done")

try:
    import tomllib  # py311+
except ImportError:  # pragma: no cover - py<311
    import tomli as tomllib


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
        data: Optional[Union[Dict[str, Any], str]] = None,
        *,
        accept: str = "application/json",
        content_type: str = "application/json",
    ) -> Any:
        url = f"{self.url}/{self.repo_full_name}/{path.lstrip('/')}"
        headers = {
            "Accept": accept,
            "Content-Type": content_type,
        }

        web_cookie = os.environ.get("GITEA_WEB_COOKIE")
        web_csrf = os.environ.get("GITEA_WEB_CSRF_TOKEN")
        if not web_csrf:
            web_csrf = self._csrf_from_cookie(web_cookie)

        if not web_cookie:
            raise RuntimeError("GITEA_WEB_COOKIE required for project board web operations")
        if not web_csrf:
            raise RuntimeError("CSRF token missing: include _csrf in GITEA_WEB_COOKIE")

        headers["Cookie"] = web_cookie
        headers["X-CSRF-Token"] = web_csrf

        req_data: Optional[bytes] = None
        if data is not None:
            if isinstance(data, str):
                req_data = data.encode("utf-8")
            elif content_type.startswith("application/json"):
                req_data = json.dumps(data).encode("utf-8")
            else:
                req_data = urlencode(data).encode("utf-8")
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
            hint = ""
            if e.code == 404 and path.startswith("projects/"):
                hint = " (possible wrong project id or no board access)"
            raise GiteaAPIError(
                message=f"Web request failed for {path}{hint}: {body[:300]}",
                code=e.code,
                reason=e.reason,
            )

    @staticmethod
    def _csrf_from_cookie(cookie: Optional[str]) -> Optional[str]:
        if not cookie:
            return None
        for part in cookie.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key.strip() == "_csrf":
                return value.strip()
        return None

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

        # Gitea 1.25+ renders extra classes and SVG wrappers in column headers.
        # The modal metadata remains stable and includes id/title pairs.
        for col_id, title in re.findall(
            r'data-modal-project-column-id="(\d+)".*?data-modal-project-column-title-input="([^"]*)"',
            html,
            flags=re.S,
        ):
            cid = int(col_id)
            if cid not in columns:
                columns[cid] = {"id": cid, "title": None, "key": None}
            title_clean = title.strip()
            if title_clean:
                columns[cid]["title"] = title_clean
                columns[cid]["key"] = self._column_key(title_clean)

        resolved = [c for c in columns.values() if c["title"]]
        resolved.sort(key=lambda c: c["id"])
        return resolved

    def list_projects(self) -> List[Dict[str, Any]]:
        html = self._web_request("GET", "issues", accept="text/html")
        if not isinstance(html, str):
            raise RuntimeError("Unexpected non-HTML response while reading repository issue page")

        pattern = (
            rf'<div class="item issue-action" data-element-id="(\d+)" '
            rf'data-url="/{re.escape(self.repo_full_name)}/issues/projects">(.*?)</div>'
        )

        projects: List[Dict[str, Any]] = []
        for pid_raw, raw_body in re.findall(pattern, html, flags=re.S):
            pid = int(pid_raw)
            if pid == 0:
                continue
            name = re.sub(r"<[^>]+>", "", raw_body).strip()
            if not name:
                continue
            projects.append({"id": pid, "name": name})

        projects.sort(key=lambda p: p["id"])
        return projects

    def list_project_cards(
        self, project_id: int, issue_number: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        html = self._web_request("GET", f"projects/{project_id}", accept="text/html")
        if not isinstance(html, str):
            raise RuntimeError("Unexpected non-HTML response while reading project board")

        cards: List[Dict[str, Any]] = []
        column_pattern = re.compile(
            r'<div class="project-column"[^>]*data-id="(\d+)"[^>]*>(.*?)(?=<div class="project-column"|<div class="ui small modal" id="project-column-modal-edit"|$)',
            flags=re.S,
        )
        issue_pattern = re.compile(
            r'class="(?:issue-card|project-card)[^"]*"[^>]*data-(?:issue|issue-id)="(\d+)".*?href="/[^"]+/issues/(\d+)"',
            flags=re.S,
        )

        for column_id_raw, block in column_pattern.findall(html):
            column_id = int(column_id_raw)
            title_match = re.search(
                rf'data-modal-project-column-id="{column_id}".*?data-modal-project-column-title-input="([^"]+)"',
                block,
                flags=re.S,
            )
            if not title_match:
                continue
            column_title = title_match.group(1).strip()
            for issue_id_raw, issue_number_raw in issue_pattern.findall(block):
                parsed_issue_number = int(issue_number_raw)
                if issue_number is not None and parsed_issue_number != issue_number:
                    continue
                cards.append(
                    {
                        "project_id": project_id,
                        "column_id": column_id,
                        "column_title": column_title,
                        "issue_id": int(issue_id_raw),
                        "issue_number": parsed_issue_number,
                    }
                )

        return cards

    def add_issue_to_project(self, project_id: int, issue_index: int) -> Dict[str, Any]:
        issue_id = self._issue_internal_id(issue_index)
        payload = f"id={project_id}"
        self._web_request(
            "POST",
            f"issues/projects?issue_ids={issue_id}",
            payload,
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
        )
        return {
            "issue_number": issue_index,
            "issue_id": issue_id,
            "project_id": project_id,
        }

    def move_issue_to_project_column(
        self, project_id: int, issue_index: int, target_column: str
    ) -> Dict[str, Any]:
        columns = self.list_project_columns(project_id)
        if not columns:
            raise RuntimeError(
                f"No columns found for project #{project_id}. "
                "Run `gt project list` to confirm project id and board visibility."
            )

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

    def find_default_kanban_project(self) -> Dict[str, Any]:
        required = {self._column_key(name) for name in DEFAULT_KANBAN_COLUMNS}
        for project in self.list_projects():
            project_id = project.get("id")
            if not isinstance(project_id, int):
                continue
            columns = self.list_project_columns(project_id)
            available = {self._column_key(column["title"]) for column in columns if column.get("title")}
            if required.issubset(available):
                return project
        raise RuntimeError(
            "No default kanban project found with columns: Backlog, To Do, In Progress, Done"
        )

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

    def list_issue_comments(self, issue_index: int) -> List[Dict[str, Any]]:
        return self._request("GET", f"issues/{issue_index}/comments", repo=self.repo_full_name) or []

    def create_issue_comment(self, issue_index: int, body: str) -> Dict[str, Any]:
        return self._request("POST", f"issues/{issue_index}/comments", {"body": body}, repo=self.repo_full_name)

    def update_issue_comment(self, comment_id: int, body: str) -> Dict[str, Any]:
        return self._request("PATCH", f"issues/comments/{comment_id}", {"body": body}, repo=self.repo_full_name)

    def upsert_issue_comment_marker(
        self, issue_index: int, marker: str, body: str
    ) -> Dict[str, Any]:
        for comment in self.list_issue_comments(issue_index):
            if marker in (comment.get("body") or ""):
                updated = self.update_issue_comment(comment["id"], body)
                return {"action": "updated", "comment_id": updated.get("id"), "comment": updated}

        created = self.create_issue_comment(issue_index, body)
        return {"action": "created", "comment_id": created.get("id"), "comment": created}

    def assign_issue(self, issue_index: int, username: str) -> Dict[str, Any]:
        return self.patch_issue(issue_index, {"assignees": [username]})

    def unassign_issue(
        self, issue_index: int, username: Optional[str] = None, all_assignees: bool = False
    ) -> Dict[str, Any]:
        if all_assignees:
            return self.patch_issue(issue_index, {"assignees": []})
        if not username:
            raise ValueError("username required unless all_assignees=True")
        issue = self.get_issue(issue_index, repo=self.repo_full_name)
        remaining = [
            assignee.get("login")
            for assignee in issue.get("assignees", [])
            if isinstance(assignee, dict) and assignee.get("login") != username
        ]
        return self.patch_issue(issue_index, {"assignees": remaining})

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

    def list_pulls(self, state: str = "open", base: Optional[str] = None, head: Optional[str] = None) -> List[Dict[str, Any]]:
        params = [f"state={state}", "limit=100"]
        if base:
            params.append(f"base={base}")
        if head:
            params.append(f"head={head}")
        return self._request("GET", f"pulls?{'&'.join(params)}") or []

    def create_pull(self, title: str, head: str, base: str = "main", body: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"title": title, "head": head, "base": base}
        if body:
            payload["body"] = body
        return self._request("POST", "pulls", payload)

    def list_pull_reviews(self, pull_index: int) -> List[Dict[str, Any]]:
        return self._request("GET", f"pulls/{pull_index}/reviews") or []

    def submit_pull_review(self, pull_index: int, event: str, body: Optional[str] = None) -> Dict[str, Any]:
        normalized_event = event.upper()
        if normalized_event not in {"APPROVED", "REQUEST_CHANGES", "COMMENT"}:
            raise ValueError("event must be one of: APPROVED, REQUEST_CHANGES, COMMENT")
        payload: Dict[str, Any] = {"event": normalized_event}
        if body:
            payload["body"] = body
        return self._request("POST", f"pulls/{pull_index}/reviews", payload)

    def merge_pull(
        self,
        pull_index: int,
        merge_method: str = "merge",
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        method = merge_method.lower()
        if method not in {"merge", "rebase", "rebase-merge", "squash"}:
            raise ValueError("merge_method must be one of: merge, rebase, rebase-merge, squash")
        payload: Dict[str, Any] = {"Do": method}
        if commit_title:
            payload["MergeTitleField"] = commit_title
        if commit_message:
            payload["MergeMessageField"] = commit_message
        return self._request("POST", f"pulls/{pull_index}/merge", payload)

    def request_pull_reviewer(self, pull_index: int, username: str) -> Dict[str, Any]:
        return self._request(
            "POST", f"pulls/{pull_index}/requested_reviewers", {"reviewers": [username]}
        )

    def close_pull(self, pull_index: int) -> Dict[str, Any]:
        return self._request("PATCH", f"pulls/{pull_index}", {"state": "closed"})

    def transition_issue_state(self, issue_index: int, target_state: str) -> Dict[str, Any]:
        normalized_state = normalize_issue_state_target(target_state)
        if normalized_state in {"Closed", "Cancelled", "Duplicate"}:
            self.patch_issue(issue_index, {"state": "closed"})
            return {
                "issue_number": issue_index,
                "state": normalized_state,
                "issue_state": "closed",
            }

        self.patch_issue(issue_index, {"state": "open"})
        project = self.find_default_kanban_project()
        project_id = project["id"]
        cards = self.list_project_cards(project_id, issue_number=issue_index)
        if not cards:
            self.add_issue_to_project(project_id, issue_index)
        moved = self.move_issue_to_project_column(project_id, issue_index, normalized_state)
        return {"issue_number": issue_index, "state": normalized_state, **moved}


def normalize_issue_state_target(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "backlog": "Backlog",
        "to do": "To Do",
        "todo": "To Do",
        "in progress": "In Progress",
        "inprogress": "In Progress",
        "done": "Done",
        "closed": "Closed",
        "cancelled": "Cancelled",
        "canceled": "Cancelled",
        "duplicate": "Duplicate",
    }
    if normalized not in aliases:
        raise ValueError(
            "state must be one of: Backlog, To Do, In Progress, Done, Closed, Cancelled, Duplicate"
        )
    return aliases[normalized]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="gt: Gitea Tool for Agentic Workflows", add_help=False)
    parser.add_argument("--url")
    parser.add_argument("--token")
    parser.add_argument("--repo")
    parser.add_argument("--remote")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)

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

    issue_parser = subparsers.add_parser("issue", help="Issue operations")
    issue_subparsers = issue_parser.add_subparsers(dest="issue_action")

    issue_comments = issue_subparsers.add_parser("comments", help="Issue comment operations")
    issue_comments_subparsers = issue_comments.add_subparsers(dest="issue_comments_action")

    issue_comments_list = issue_comments_subparsers.add_parser(
        "list", help="List comments on an issue"
    )
    issue_comments_list.add_argument("issue", type=int)

    issue_comments_upsert = issue_comments_subparsers.add_parser(
        "upsert-marker", help="Create or update a marker comment"
    )
    issue_comments_upsert.add_argument("issue", type=int)
    issue_comments_upsert.add_argument("--marker", required=True)
    issue_comments_upsert.add_argument("--file")

    issue_assign = issue_subparsers.add_parser("assign", help="Assign a user to an issue")
    issue_assign.add_argument("issue", type=int)
    issue_assign.add_argument("username")

    issue_unassign = issue_subparsers.add_parser("unassign", help="Remove assignees from an issue")
    issue_unassign.add_argument("issue", type=int)
    issue_unassign.add_argument("username", nargs="?")
    issue_unassign.add_argument("--all", action="store_true")

    issue_state = issue_subparsers.add_parser("state", help="Move issue through kanban workflow")
    issue_state.add_argument("issue", type=int)
    issue_state.add_argument("--to", required=True)

    # Project board operations (web-routed in Gitea)
    project_parser = subparsers.add_parser("project", help="Project board operations")
    project_subparsers = project_parser.add_subparsers(dest="project_action")

    project_subparsers.add_parser("list", help="List repository project boards")

    project_cols = project_subparsers.add_parser("columns", help="List project columns")
    project_cols.add_argument("project_id", type=int)

    project_add = project_subparsers.add_parser(
        "add", help="Add an issue card to a project board"
    )
    project_add.add_argument("project_id", type=int)
    project_add.add_argument("issue", type=int, help="Issue number (index)")

    project_move = project_subparsers.add_parser(
        "move", help="Move issue card to a project column"
    )
    project_move.add_argument("project_id", type=int)
    project_move.add_argument("issue", type=int, help="Issue number (index)")
    project_move.add_argument(
        "--to", required=True, help="Target column name (e.g. 'To Do', 'In Progress')"
    )

    project_cards = project_subparsers.add_parser("cards", help="List project cards and issue membership")
    project_cards.add_argument("project_id", type=int)
    project_cards.add_argument("--issue", type=int)

    # Pull request operations
    pr_parser = subparsers.add_parser("pr", help="Pull request operations")
    pr_subparsers = pr_parser.add_subparsers(dest="pr_action")

    pr_list = pr_subparsers.add_parser("list", help="List pull requests")
    pr_list.add_argument("--state", default="open", choices=["open", "closed", "all"])
    pr_list.add_argument("--base", help="Filter by base branch")
    pr_list.add_argument("--head", help="Filter by head branch")

    pr_create = pr_subparsers.add_parser("create", help="Create a pull request")
    pr_create.add_argument("--title", required=True, help="PR title")
    pr_create.add_argument("--head", required=True, help="Head branch name")
    pr_create.add_argument("--base", default="main", help="Base branch name")
    pr_create.add_argument("--body", help="PR description body")

    pr_reviews = pr_subparsers.add_parser("reviews", help="List pull request reviews")
    pr_reviews.add_argument("pull", type=int, help="Pull request number")

    pr_review = pr_subparsers.add_parser("review", help="Submit a pull request review")
    pr_review.add_argument("pull", type=int, help="Pull request number")
    pr_review.add_argument(
        "--event",
        required=True,
        choices=["APPROVED", "REQUEST_CHANGES", "COMMENT"],
        help="Review event type",
    )
    pr_review.add_argument("--body", help="Review comment body")

    pr_merge = pr_subparsers.add_parser("merge", help="Merge a pull request")
    pr_merge.add_argument("pull", type=int, help="Pull request number")
    pr_merge.add_argument(
        "--method",
        default="merge",
        choices=["merge", "rebase", "rebase-merge", "squash"],
        help="Merge strategy",
    )
    pr_merge.add_argument("--title", help="Merge commit title")
    pr_merge.add_argument("--message", help="Merge commit message")

    pr_request_reviewer = pr_subparsers.add_parser(
        "request-reviewer", help="Request a reviewer on a pull request"
    )
    pr_request_reviewer.add_argument("pull", type=int, help="Pull request number")
    pr_request_reviewer.add_argument("username")

    pr_close = pr_subparsers.add_parser("close", help="Close a pull request without merging")
    pr_close.add_argument("pull", type=int, help="Pull request number")
    return parser


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.command == "issue" and args.issue_action == "unassign":
        if args.all and args.username:
            raise ValueError("issue unassign does not accept both a username and --all")
        if not args.all and not args.username:
            raise ValueError("issue unassign requires a username or --all")


def load_gt_config(path: str) -> Dict[str, Any]:
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return {}

    with open(expanded, "rb") as fh:
        raw = tomllib.load(fh)
    if not isinstance(raw, dict):
        return {}
    remotes = raw.get("remotes")
    if remotes is None or not isinstance(remotes, dict):
        raw["remotes"] = {}
    return raw


def read_body_from_args(file_path: Optional[str] = None) -> str:
    if file_path:
        with open(file_path, "r", encoding="utf-8") as fh:
            body = fh.read()
    else:
        body = sys.stdin.read()
    if not body:
        raise ValueError("comment body required via --file or stdin")
    return body


def _repo_from_remote(remote_cfg: Dict[str, Any]) -> Optional[str]:
    repo = remote_cfg.get("repo")
    if isinstance(repo, str) and "/" in repo:
        return repo
    owner = remote_cfg.get("owner")
    if isinstance(owner, str) and isinstance(repo, str) and owner and repo:
        return f"{owner}/{repo}"
    return None


def resolve_connection_settings(args: argparse.Namespace, config: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[str]]:
    env = os.environ
    config_data = config if config is not None else load_gt_config(args.config)

    remotes = config_data.get("remotes", {}) if isinstance(config_data, dict) else {}
    remote_name = args.remote or (config_data.get("default_remote") if isinstance(config_data, dict) else None)
    remote_cfg = {}
    if remote_name:
        remote_cfg = remotes.get(remote_name, {})
        if not isinstance(remote_cfg, dict):
            raise ValueError(
                f"Remote '{remote_name}' not found in {os.path.expanduser(args.config)} under [remotes.{remote_name}]"
            )
        if not remote_cfg:
            raise ValueError(
                f"Remote '{remote_name}' not found in {os.path.expanduser(args.config)} under [remotes.{remote_name}]"
            )

    cfg_token = remote_cfg.get("token")
    token_env_name = remote_cfg.get("token_env")
    cfg_token_env = env.get(token_env_name) if isinstance(token_env_name, str) and token_env_name else None
    cfg_web_cookie = remote_cfg.get("web_cookie")
    web_cookie_env_name = remote_cfg.get("web_cookie_env")
    cfg_web_cookie_env = env.get(web_cookie_env_name) if isinstance(web_cookie_env_name, str) and web_cookie_env_name else None

    settings: Dict[str, Optional[str]] = {
        "url": remote_cfg.get("url") if isinstance(remote_cfg.get("url"), str) else None,
        "repo": _repo_from_remote(remote_cfg),
        "token": cfg_token if isinstance(cfg_token, str) else cfg_token_env,
        "web_cookie": cfg_web_cookie if isinstance(cfg_web_cookie, str) else cfg_web_cookie_env,
        "remote": remote_name,
    }

    if env.get("GITEA_URL"):
        settings["url"] = env.get("GITEA_URL")
    if env.get("GITEA_REPO"):
        settings["repo"] = env.get("GITEA_REPO")
    if env.get("GITEA_TOKEN"):
        settings["token"] = env.get("GITEA_TOKEN")
    if env.get("GITEA_WEB_COOKIE"):
        settings["web_cookie"] = env.get("GITEA_WEB_COOKIE")

    if args.url:
        settings["url"] = args.url
    if args.repo:
        settings["repo"] = args.repo
    if args.token:
        settings["token"] = args.token

    if not settings["url"]:
        settings["url"] = DEFAULT_GITEA_URL
    return settings


def main():
    parser = build_parser()

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
                    {"name": "issue comments list", "description": "List comments on an issue", "usage": "gt issue comments list <issue_number>"},
                    {"name": "issue comments upsert-marker", "description": "Create or update a marker comment", "usage": "gt issue comments upsert-marker <issue_number> --marker \"## Codex Workpad\" [--file body.md]"},
                    {"name": "issue assign", "description": "Assign a user to an issue", "usage": "gt issue assign <issue_number> <username>"},
                    {"name": "issue unassign", "description": "Remove assignees from an issue", "usage": "gt issue unassign <issue_number> <username>|--all"},
                    {"name": "issue state", "description": "Move issue through kanban workflow", "usage": "gt issue state <issue_number> --to \"In Progress\""},
                    {"name": "label add", "description": "Add label to issue", "usage": "gt label add <issue_index> <label_id>"},
                    {"name": "label rm", "description": "Remove label from issue", "usage": "gt label rm <issue_index> <label_id>"},
                    {"name": "project list", "description": "List repository project boards", "usage": "gt project list"},
                    {"name": "project columns", "description": "List columns for a project board", "usage": "gt project columns <project_id>"},
                    {"name": "project cards", "description": "List project cards and issue membership", "usage": "gt project cards <project_id> [--issue <issue_number>]"},
                    {"name": "project add", "description": "Add an issue card to a board", "usage": "gt project add <project_id> <issue_number>"},
                    {"name": "project move", "description": "Move issue card to a board column", "usage": "gt project move <project_id> <issue_number> --to \"In Progress\""},
                    {"name": "pr list", "description": "List pull requests", "usage": "gt pr list [--state open|closed|all]"},
                    {"name": "pr create", "description": "Create a pull request", "usage": "gt pr create --title \"...\" --head branch --base main [--body \"...\"]"},
                    {"name": "pr reviews", "description": "List reviews on a pull request", "usage": "gt pr reviews <pr_number>"},
                    {"name": "pr review", "description": "Submit a pull request review", "usage": "gt pr review <pr_number> --event APPROVED|REQUEST_CHANGES|COMMENT [--body \"...\"]"},
                    {"name": "pr request-reviewer", "description": "Request a reviewer on a pull request", "usage": "gt pr request-reviewer <pr_number> <username>"},
                    {"name": "pr close", "description": "Close a pull request without merging", "usage": "gt pr close <pr_number>"},
                    {"name": "pr merge", "description": "Merge a pull request", "usage": "gt pr merge <pr_number> [--method merge|rebase|rebase-merge|squash]"},
                ]
            },
            next_actions=[
                {"command": "gt list", "description": "List all open issues"},
            ]
        )
        print(dump_yaml(payload))
        return

    args = parse_args()
    try:
        validate_args(args)
        settings = resolve_connection_settings(args)
    except ValueError as e:
        payload = envelope(
            command="gt",
            ok=False,
            error={"message": str(e)},
            fix=f"Create or update {os.path.expanduser(args.config)} and set a valid remote.",
        )
        print(dump_yaml(payload))
        sys.exit(1)

    if not settings["token"]:
        payload = envelope(
            command="gt",
            ok=False,
            error={"message": "Gitea token required"},
            fix="Set GITEA_TOKEN, pass --token, or configure token/token_env in ~/.config/carapace/gt.toml.",
        )
        print(dump_yaml(payload))
        sys.exit(1)

    if not settings["repo"]:
        payload = envelope(
            command="gt",
            ok=False,
            error={"message": "Gitea repo required"},
            fix="Set GITEA_REPO, pass --repo, or configure owner/repo in ~/.config/carapace/gt.toml.",
        )
        print(dump_yaml(payload))
        sys.exit(1)

    client = GiteaClient(settings["url"], settings["token"], settings["repo"])
    if settings.get("web_cookie"):
        os.environ["GITEA_WEB_COOKIE"] = settings["web_cookie"]
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

        elif args.command == "issue":
            if args.issue_action == "comments":
                if args.issue_comments_action == "list":
                    comments = client.list_issue_comments(args.issue)
                    result_comments = [
                        {
                            "id": comment.get("id"),
                            "body": comment.get("body"),
                            "user": (comment.get("user") or {}).get("login"),
                            "created_at": comment.get("created_at"),
                            "updated_at": comment.get("updated_at"),
                        }
                        for comment in comments
                    ]
                    payload = envelope(
                        command=full_cmd,
                        ok=True,
                        result={"issue": args.issue, "comments": result_comments, "count": len(result_comments)},
                        next_actions=[
                            {
                                "command": f"gt issue comments upsert-marker {args.issue} --marker \"## Codex Workpad\"",
                                "description": "Create or update a rolling workpad comment",
                            }
                        ],
                    )
                    print(dump_yaml(payload))
                elif args.issue_comments_action == "upsert-marker":
                    body = read_body_from_args(args.file)
                    result = client.upsert_issue_comment_marker(args.issue, args.marker, body)
                    payload = envelope(
                        command=full_cmd,
                        ok=True,
                        result={"issue": args.issue, **result},
                        next_actions=[
                            {
                                "command": f"gt issue comments list {args.issue}",
                                "description": "List issue comments",
                            }
                        ],
                    )
                    print(dump_yaml(payload))
                else:
                    parser.print_help()
            elif args.issue_action == "assign":
                issue = client.assign_issue(args.issue, args.username)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "issue": {
                            "number": issue.get("number"),
                            "assignees": [
                                assignee.get("login")
                                for assignee in issue.get("assignees", [])
                                if isinstance(assignee, dict)
                            ],
                        }
                    },
                    next_actions=[
                        {
                            "command": f"gt issue unassign {args.issue} {args.username}",
                            "description": "Remove this assignee",
                        }
                    ],
                )
                print(dump_yaml(payload))
            elif args.issue_action == "unassign":
                issue = client.unassign_issue(args.issue, args.username, all_assignees=args.all)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "issue": {
                            "number": issue.get("number"),
                            "assignees": [
                                assignee.get("login")
                                for assignee in issue.get("assignees", [])
                                if isinstance(assignee, dict)
                            ],
                        }
                    },
                    next_actions=[
                        {"command": f"gt issue assign {args.issue} <username>", "description": "Assign a user to this issue"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.issue_action == "state":
                result = client.transition_issue_state(args.issue, args.to)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result=result,
                    next_actions=[
                        {
                            "command": f"gt project cards {result.get('project_id')}" if result.get("project_id") else f"gt list --state closed",
                            "description": "Inspect related board or issue state",
                        }
                    ],
                )
                print(dump_yaml(payload))
            else:
                parser.print_help()

        elif args.command == "project":
            if args.project_action == "list":
                projects = client.list_projects()
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"projects": projects},
                    next_actions=[
                        {"command": "gt project columns <project_id>", "description": "List board columns"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.project_action == "columns":
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
            elif args.project_action == "add":
                added = client.add_issue_to_project(args.project_id, args.issue)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"message": "Issue added to project board", **added},
                    next_actions=[
                        {"command": f"gt project move {args.project_id} {args.issue} --to \"Backlog\"", "description": "Move issue to a board column"},
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
            elif args.project_action == "cards":
                cards = client.list_project_cards(args.project_id, issue_number=args.issue)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"project_id": args.project_id, "cards": cards, "count": len(cards)},
                    next_actions=[
                        {"command": f"gt project columns {args.project_id}", "description": "List project columns"},
                    ],
                )
                print(dump_yaml(payload))
            else:
                parser.print_help()

        elif args.command == "pr":
            if args.pr_action == "list":
                pulls = client.list_pulls(state=args.state, base=args.base, head=args.head)
                result_pulls = [
                    {
                        "number": p.get("number"),
                        "state": p.get("state"),
                        "title": p.get("title"),
                        "head": (p.get("head") or {}).get("ref"),
                        "base": (p.get("base") or {}).get("ref"),
                        "merged": p.get("merged"),
                        "html_url": p.get("html_url"),
                    }
                    for p in pulls
                ]
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"pulls": result_pulls, "count": len(result_pulls)},
                    next_actions=[
                        {"command": "gt pr create --title \"...\" --head feature-branch --base main", "description": "Create a new pull request"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.pr_action == "create":
                pr = client.create_pull(title=args.title, head=args.head, base=args.base, body=args.body)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "message": "Pull request created",
                        "number": pr.get("number"),
                        "url": pr.get("html_url"),
                        "state": pr.get("state"),
                    },
                    next_actions=[
                        {"command": f"gt pr reviews {pr.get('number')}", "description": "List current reviews"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.pr_action == "reviews":
                reviews = client.list_pull_reviews(args.pull)
                result_reviews = [
                    {
                        "id": r.get("id"),
                        "state": r.get("state"),
                        "submitted_at": r.get("submitted_at"),
                        "user": (r.get("user") or {}).get("login"),
                        "body": r.get("body"),
                    }
                    for r in reviews
                ]
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={"pull": args.pull, "reviews": result_reviews, "count": len(result_reviews)},
                    next_actions=[
                        {"command": f"gt pr review {args.pull} --event APPROVED --body \"looks good\"", "description": "Approve the pull request"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.pr_action == "review":
                review = client.submit_pull_review(args.pull, args.event, args.body)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "message": "Pull request review submitted",
                        "pull": args.pull,
                        "review_id": review.get("id"),
                        "state": review.get("state"),
                    },
                    next_actions=[
                        {"command": f"gt pr reviews {args.pull}", "description": "List current reviews"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.pr_action == "merge":
                merged = client.merge_pull(
                    args.pull,
                    merge_method=args.method,
                    commit_title=args.title,
                    commit_message=args.message,
                )
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "message": "Pull request merged",
                        "pull": args.pull,
                        "sha": merged.get("sha"),
                        "merged": merged.get("merged", True),
                    },
                    next_actions=[
                        {"command": "gt pr list --state open", "description": "List remaining open pull requests"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.pr_action == "request-reviewer":
                review_request = client.request_pull_reviewer(args.pull, args.username)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "pull": args.pull,
                        "requested_reviewers": [
                            reviewer.get("login")
                            for reviewer in review_request.get("requested_reviewers", [])
                            if isinstance(reviewer, dict)
                        ],
                    },
                    next_actions=[
                        {"command": f"gt pr reviews {args.pull}", "description": "List current reviews"},
                    ],
                )
                print(dump_yaml(payload))
            elif args.pr_action == "close":
                closed = client.close_pull(args.pull)
                payload = envelope(
                    command=full_cmd,
                    ok=True,
                    result={
                        "pull": args.pull,
                        "state": closed.get("state"),
                    },
                    next_actions=[
                        {"command": "gt pr list --state closed", "description": "List closed pull requests"},
                    ],
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
    except ValueError as e:
        payload = envelope(
            command=full_cmd,
            ok=False,
            error={"message": str(e), "type": type(e).__name__},
            fix="Check the command arguments and valid workflow state names.",
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
