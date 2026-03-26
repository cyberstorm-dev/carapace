import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Union
from urllib import error, request

from carapace.issue_ref import IssueRef, parse_dependency_refs, parse_issue_ref
from carapace.hateoas import envelope, dump_yaml

DEFAULT_GITEA_URL = "http://100.73.228.90:3000"
DEFAULT_CONFIG_PATH = "~/.config/carapace/gt.toml"

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
    label_parser.add_argument("action", choices=["add"])
    label_parser.add_argument("issue", type=int)
    label_parser.add_argument("label_id", type=int)
    return parser


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    return parser.parse_args(argv)


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

    settings: Dict[str, Optional[str]] = {
        "url": remote_cfg.get("url") if isinstance(remote_cfg.get("url"), str) else None,
        "repo": _repo_from_remote(remote_cfg),
        "token": cfg_token if isinstance(cfg_token, str) else cfg_token_env,
        "remote": remote_name,
    }

    if env.get("GITEA_URL"):
        settings["url"] = env.get("GITEA_URL")
    if env.get("GITEA_REPO"):
        settings["repo"] = env.get("GITEA_REPO")
    if env.get("GITEA_TOKEN"):
        settings["token"] = env.get("GITEA_TOKEN")

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
                    {"name": "label add", "description": "Add label to issue", "usage": "gt label add <issue_index> <label_id>"},
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
