from __future__ import annotations

from typing import Any, Dict, List, Optional

import yaml


Envelope = Dict[str, Any]


def envelope(
    *,
    command: str,
    ok: bool,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[Dict[str, Any]] = None,
    fix: Optional[str] = None,
    next_actions: Optional[List[Dict[str, str]]] = None,
) -> Envelope:
    payload: Envelope = {
        "ok": ok,
        "command": command,
        "next_actions": next_actions or [],
    }
    if ok:
        payload["result"] = result or {}
    else:
        payload["error"] = error or {}
        if fix:
            payload["fix"] = fix
    return payload


def dump_yaml(payload: Envelope) -> str:
    return yaml.safe_dump(payload, sort_keys=False)
