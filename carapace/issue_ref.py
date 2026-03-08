from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional


@dataclass(frozen=True, order=True)
class IssueRef:
    """Canonical issue identity used in cross-repo dependency graphs."""

    repo: str
    number: int

    def display(self, local_repo: Optional[str] = None) -> str:
        if local_repo and self.repo == local_repo:
            return f"#{self.number}"
        return f"{self.repo}#{self.number}"


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _repo_from_payload(payload: dict[str, Any]) -> Optional[str]:
    repository = payload.get("repository")
    if isinstance(repository, dict):
        full_name = repository.get("full_name")
        if full_name:
            return str(full_name)

        owner = repository.get("owner")
        if isinstance(owner, dict):
            owner_name = owner.get("login") or owner.get("username")
            repo_name = repository.get("name")
            if owner_name and repo_name:
                return f"{owner_name}/{repo_name}"

    owner = payload.get("owner")
    repo_name = payload.get("repo")
    if isinstance(owner, dict):
        owner = owner.get("login") or owner.get("username")
    if owner and repo_name:
        return f"{owner}/{repo_name}"

    return None


def parse_issue_ref(value: Any, default_repo: str = "local") -> Optional[IssueRef]:
    if isinstance(value, IssueRef):
        return value

    if isinstance(value, int):
        return IssueRef(repo=default_repo, number=value)

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if "#" in candidate:
            repo, raw = candidate.rsplit("#", 1)
            repo_name = repo.strip() or default_repo
            return IssueRef(repo=repo_name, number=int(raw))
        return IssueRef(repo=default_repo, number=int(candidate))

    if isinstance(value, dict):
        number = _coerce_int(value.get("number"))
        if number is None:
            number = _coerce_int(value.get("index"))
        if number is None:
            return None

        repo = _repo_from_payload(value) or default_repo
        return IssueRef(repo=repo, number=number)

    return None


def parse_dependency_refs(dependencies: Any, default_repo: str = "local") -> List[IssueRef]:
    if not isinstance(dependencies, Iterable) or isinstance(dependencies, (bytes, str, dict)):
        return []
    out: List[IssueRef] = []
    for dep in dependencies:
        ref = parse_issue_ref(dep, default_repo=default_repo)
        if ref is not None:
            out.append(ref)
    return out
