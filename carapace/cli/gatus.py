import argparse
import json
import sys
from typing import Any, Dict, List, Optional
from urllib import request, error

from carapace.hateoas import envelope


def run_gatus_check(
    gatus_url: str,
    required_nodes: List[str],
    skip_groups: Optional[List[str]] = None,
) -> Dict[str, Any]:
    url = f"{gatus_url.rstrip('/')}/api/v1/endpoints/statuses"
    try:
        req = request.Request(url)
        with request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.URLError as e:
        raise RuntimeError(f"Failed to fetch Gatus statuses from {url}: {e}")

    failed_endpoints = []
    total_checked = 0
    skip_groups = set(skip_groups or [])

    for endpoint in data:
        name = endpoint.get("name", "")
        group = endpoint.get("group", "")
        results = endpoint.get("results", [])

        if group in skip_groups:
            continue
        
        # Filter for relevant endpoints. We only care if the endpoint name or group contains our target nodes.
        # This prevents failing the build due to dev nodes (like infralink*) being down.
        is_relevant = False
        for node in required_nodes:
            if node in name or node in group:
                is_relevant = True
                break
                
        if not is_relevant:
            continue
            
        total_checked += 1
        
        # results[0] is the most recent health check result
        if results and not results[0].get("success", False):
            failed_endpoints.append({
                "name": name,
                "group": group,
                "status": "unhealthy",
                "errors": results[0].get("errors", [])
            })

    if failed_endpoints:
        return {
            "ok": False,
            "total_checked": total_checked,
            "failed_count": len(failed_endpoints),
            "failures": failed_endpoints
        }
    
    return {
        "ok": True,
        "total_checked": total_checked,
        "message": "All relevant endpoints are healthy"
    }


def run(args: argparse.Namespace) -> int:
    try:
        # Default nodes to check if none provided
        nodes = args.nodes.split(",") if args.nodes else ["cyberstorm-citadel", "cyberstorm-watchtower"]
        skip_groups = args.skip_groups.split(",") if getattr(args, "skip_groups", None) else ["dns"]
        result = run_gatus_check(args.gatus_url, nodes, skip_groups=skip_groups)
        
        payload = envelope(
            command="carapace gatus-check",
            ok=result["ok"],
            result=result if result["ok"] else None,
            error=result if not result["ok"] else None,
            next_actions=[]
        )
        return payload, 0 if result["ok"] else 1
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        payload = envelope(
            command="carapace gatus-check",
            ok=False,
            error={"message": str(e), "type": type(e).__name__},
            fix="Check Gatus URL and network connectivity"
        )
        return payload, 1
