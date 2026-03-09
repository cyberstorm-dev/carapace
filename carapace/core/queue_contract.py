import json
from typing import Any, Dict, List, Optional, Tuple

from carapace.issue_ref import IssueRef

KNOWN_FORGES = {"gitea", "github"}


def split_issue_locator(locator: str, default_forge: str = "gitea") -> Tuple[str, str]:
    candidate = (locator or "").strip()
    if "://" in candidate:
        scheme, rest = candidate.split("://", 1)
        if scheme in KNOWN_FORGES and rest:
            return scheme, rest
    if ":" in candidate:
        prefix, rest = candidate.split(":", 1)
        if prefix in KNOWN_FORGES and rest:
            return prefix, rest
    return default_forge, candidate


def identity_from_ref(ref: IssueRef, default_forge: str = "gitea") -> Dict[str, Any]:
    forge, repo = split_issue_locator(ref.repo, default_forge=default_forge)
    return {"forge": forge, "repo": repo, "number": int(ref.number)}


def identity_to_ref(identity: Dict[str, Any], default_forge: str = "gitea") -> Optional[IssueRef]:
    if not isinstance(identity, dict):
        return None
    forge = identity.get("forge") or default_forge
    repo = identity.get("repo")
    number = identity.get("number")
    if not repo or number is None:
        return None
    try:
        parsed_number = int(number)
    except (TypeError, ValueError):
        return None
    if forge in KNOWN_FORGES:
        return IssueRef(f"{forge}:{repo}", parsed_number)
    return IssueRef(str(repo), parsed_number)


def encode_queue_member(item: Dict[str, Any]) -> str:
    return json.dumps(item, sort_keys=True, separators=(",", ":"))


def decode_queue_member(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        return None
    if "repo" not in identity or "number" not in identity:
        return None
    return payload


def issue_ref_tuple(item: Dict[str, Any], default_forge: str = "gitea") -> Optional[Tuple[str, str, int]]:
    identity = item.get("identity")
    if not isinstance(identity, dict):
        return None
    forge = str(identity.get("forge") or default_forge)
    repo = identity.get("repo")
    number = identity.get("number")
    if repo is None or number is None:
        return None
    try:
        return forge, str(repo), int(number)
    except (TypeError, ValueError):
        return None


def build_next_actions(upstream: List[Dict[str, Any]], downstream: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if upstream:
        actions.append({"action": "inspect_upstream", "issues": upstream})
    else:
        actions.append({"action": "begin_work"})
    if downstream:
        actions.append({"action": "inspect_downstream", "issues": downstream})
    return actions
