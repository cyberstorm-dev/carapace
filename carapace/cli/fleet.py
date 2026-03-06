import argparse
import json
import os
import sys
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

from carapace.hateoas import envelope


def load_registry(registry_path: Path) -> Dict[str, Any]:
    hosts_dir = registry_path / "hosts"
    if not hosts_dir.exists():
        raise FileNotFoundError(f"Registry hosts directory not found at {hosts_dir}")

    registry = {"hosts": {}}
    
    # Each host is a directory with a manifest.yml
    for host_dir in hosts_dir.iterdir():
        if not host_dir.is_dir():
            continue
        
        manifest_path = host_dir / "manifest.yml"
        if manifest_path.exists():
            try:
                with open(manifest_path, "r") as f:
                    data = yaml.safe_load(f)
                    if data and "hosts" in data:
                        registry["hosts"].update(data["hosts"])
            except Exception as e:
                print(f"Warning: Failed to load manifest at {manifest_path}: {e}", file=sys.stderr)
                
    return registry


def get_fleet_status(registry_path: Path, node_filter: Optional[str] = None) -> Dict[str, Any]:
    registry = load_registry(registry_path)
    hosts = registry.get("hosts", {})
    
    nodes = []
    for uuid, data in hosts.items():
        name = data.get("tailscale_name") or data.get("canonical_name") or uuid
        if node_filter and node_filter not in name:
            continue
            
        nodes.append({
            "uuid": uuid,
            "name": name,
            "ip": data.get("tailscale_ip"),
            "group": data.get("group"),
            "cloud": data.get("cloud"),
            "status": data.get("status"),
            "services": data.get("services", [])
        })
        
    return {
        "nodes": nodes,
        "count": len(nodes)
    }


from carapace.cli.gatus import run_gatus_check


def get_fleet_health(registry_path: Path, gatus_url: str, node_filter: Optional[str] = None) -> Dict[str, Any]:
    registry = load_registry(registry_path)
    hosts = registry.get("hosts", {})
    
    # Get overall health from Gatus
    # We want ALL production nodes by default if no filter
    target_nodes = [node_filter] if node_filter else ["cyberstorm-citadel", "cyberstorm-watchtower"]
    health_data = run_gatus_check(gatus_url, target_nodes)
    
    nodes_health = []
    for uuid, data in hosts.items():
        name = data.get("tailscale_name") or data.get("canonical_name") or uuid
        if node_filter and node_filter not in name:
            continue
            
        # Only check health for the nodes we care about for now (production)
        if not node_filter and name not in ["cyberstorm-citadel", "cyberstorm-watchtower"]:
            continue

        # Find failures for this specific node
        node_failures = []
        if not health_data["ok"]:
            for failure in health_data.get("failures", []):
                if name in failure["name"]:
                    node_failures.append(failure)
        
        nodes_health.append({
            "name": name,
            "status": "healthy" if not node_failures else "unhealthy",
            "failures": node_failures
        })
        
    return {
        "ok": all(n["status"] == "healthy" for n in nodes_health),
        "nodes": nodes_health,
        "gatus_url": gatus_url
    }


import subprocess


def get_fleet_diagram(registry_path: Path, group: Optional[str] = None) -> Dict[str, Any]:
    # Use infralink directly to generate mermaid diagram
    # We assume infralink is in the expected relative path
    infralink_dir = Path(__file__).resolve().parent.parent.parent.parent / "infra-management" / "third-party" / "infralink"
    
    if not infralink_dir.exists():
        raise FileNotFoundError(f"Infralink directory not found at {infralink_dir}")

    cmd = [
        sys.executable, "-m", "infralink",
        "--registry", str(registry_path),
        "diagram", "--format", "mermaid", "--stdout"
    ]
    if group:
        cmd.extend(["--group", group])

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{infralink_dir}/src"

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, env=env)
        # Parse the JSON response from infralink
        data = json.loads(result.stdout)
        if data.get("ok"):
            # infralink returns ok_envelope with results in 'result'
            infralink_result = data.get("result", {})
            outputs = infralink_result.get("outputs", [])
            mermaid_content = ""
            for out in outputs:
                if out.get("format") == "mermaid":
                    mermaid_content = out.get("content", "")
                    break
            
            return {
                "diagram": mermaid_content,
                "format": "mermaid",
                "group": group or "all"
            }
        else:
             raise RuntimeError(f"Infralink reported error: {data.get('error', {}).get('message')}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Infralink diagram generation failed: {e.stderr or e.stdout}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse infralink output as JSON: {result.stdout}")


def run(args: argparse.Namespace) -> int:
    registry_path = Path(args.registry_path or os.environ.get("INFRA_REGISTRY_PATH", "../infra-registry"))
    gatus_url = getattr(args, "gatus_url", None) or os.environ.get("GATUS_URL", "http://100.123.0.63:3003")
    
    try:
        if not registry_path.exists():
            registry_path = Path("registry")
            
        if not registry_path.exists():
             raise FileNotFoundError(f"Registry not found at {registry_path}. Use --registry-path.")

        if args.subcommand == "status":
            result = get_fleet_status(registry_path, args.node)
            command_str = "carapace fleet status"
        elif args.subcommand == "health":
            result = get_fleet_health(registry_path, gatus_url, args.node)
            command_str = "carapace fleet health"
        elif args.subcommand == "diagram":
            result = get_fleet_diagram(registry_path, args.group)
            command_str = "carapace fleet diagram"
        else:
            raise ValueError(f"Unknown subcommand {args.subcommand}")
        
        payload = envelope(
            command=command_str,
            ok=result.get("ok", True) if args.subcommand == "health" else True,
            result=result if result.get("ok", True) else None,
            error=result if not result.get("ok", True) else None,
            next_actions=[]
        )
        return payload, 0 if payload["ok"] else 1
        
    except Exception as e:
        payload = envelope(
            command="carapace fleet status",
            ok=False,
            error={"message": str(e), "type": type(e).__name__},
            fix="Ensure --registry-path points to the infra-registry repository"
        )
        return payload, 1
