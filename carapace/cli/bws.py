import argparse
import json
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

from carapace.hateoas import dump_yaml, envelope

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class ArgparseError(Exception):
    """Raised when CLI arguments are invalid."""


class YamlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgparseError(message)


def resolve_project_id(value: str) -> str:
    # For now, just require a UUID directly since carapace isn't tied to infra-management's ansible inventory
    if UUID_RE.match(value):
        return value
    raise ValueError(f"Project ID must be a UUID: {value}")


MAX_FIELD_LENGTH = 120


def _protect_text(value: Any, max_len: int = MAX_FIELD_LENGTH) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}… (truncated {len(text) - max_len} chars)"


def parse_secret(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id"),
        "key": item.get("key"),
        "value": _protect_text(item.get("value")),
        "note": _protect_text(item.get("note")),
    }


def run_bws(args: List[str], input_text: Optional[str] = None) -> str:
    result = subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def list_secrets(project_id: str) -> List[Dict[str, Any]]:
    output = run_bws(["bws", "secret", "list", project_id])
    items = json.loads(output or "[]")
    return [parse_secret(item) for item in items]


def get_secret(project_id: str, key: str) -> Dict[str, Any]:
    for secret in list_secrets(project_id):
        if secret.get("key") == key:
            return secret
    raise KeyError(f"Secret not found: {key}")


def set_secret(project_id: str, key: str, value: str, note: str) -> Dict[str, Any]:
    if not note:
        raise ValueError("note is required for set")
    existing = None
    for secret in list_secrets(project_id):
        if secret.get("key") == key:
            existing = secret
            break
    if existing:
        output = run_bws(["bws", "secret", "edit", "--value", value, "--note", note, existing["id"]])
        return parse_secret(json.loads(output))
    output = run_bws(["bws", "secret", "create", key, value, project_id, "--note", note])
    return parse_secret(json.loads(output))


def delete_secret(project_id: str, key: str) -> bool:
    secret = get_secret(project_id, key)
    run_bws(["bws", "secret", "delete", secret["id"]])
    return True


def _command_string(argv: List[str]) -> str:
def _command_string(argv: List[str], command_prefix: str = "carapace-bws") -> str:
    return " ".join([command_prefix] + argv).strip()


def _root_result() -> Dict[str, Any]:
    return {
        "commands": [
            {"command": "carapace bws list <project_uuid>", "description": "List secrets in a project"},
            {"command": "carapace bws get <project_uuid> <key>", "description": "Get a specific secret"},
            {
                "command": "carapace bws set <project_uuid> <key> <value> --note '<reason>'",
                "description": "Create or update a secret with required note",
            },
            {"command": "carapace bws delete <project_uuid> <key>", "description": "Delete a secret by key"},
        ],
    }


def _next_actions(command: str, project: Optional[str]) -> List[Dict[str, str]]:
    project = project or "<project_uuid>"
    actions = {
        "list": [
            {"command": f"carapace bws get {project} <key>", "description": "Get a specific secret"},
            {"command": f"carapace bws set {project} <key> <value> --note '<reason>'", "description": "Create or update a secret"},
            {"command": f"carapace bws delete {project} <key>", "description": "Delete a secret"},
        ],
        "get": [
            {"command": f"carapace bws list {project}", "description": "List all secrets"},
            {"command": f"carapace bws set {project} <key> <value> --note '<reason>'", "description": "Update the secret"},
        ],
        "set": [
            {"command": f"carapace bws get {project} <key>", "description": "Verify the updated secret"},
            {"command": f"carapace bws list {project}", "description": "List all secrets"},
        ],
        "delete": [
            {"command": f"carapace bws list {project}", "description": "Verify deletion"},
        ],
        "root": [
            {"command": "carapace bws list <project_uuid>", "description": "List secrets for a project"},
        ],
        "error": [
            {"command": f"carapace bws list {project}", "description": "List secrets for a project"},
            {"command": "carapace bws", "description": "Show command tree"},
        ],
    }
    return actions.get(command, [])


def _safe_error(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    return _protect_text(str(message))


def run_cli(argv: List[str], command_prefix: str = "carapace-bws") -> Dict[str, Any]:
    if not argv or argv in (["-h"], ["--help"]):
        return envelope(
            command=command_prefix,
            ok=True,
            result=_root_result(),
            next_actions=_next_actions("root", None),
        )

    parser = YamlArgumentParser(description="BWS convenience wrapper for carapace", add_help=False)
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("project")

    get_cmd = sub.add_parser("get")
    get_cmd.add_argument("project")
    get_cmd.add_argument("key")

    set_cmd = sub.add_parser("set")
    set_cmd.add_argument("project")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    set_cmd.add_argument("--note", required=True)

    del_cmd = sub.add_parser("delete")
    del_cmd.add_argument("project")
    del_cmd.add_argument("key")

        command_str = _command_string(argv, command_prefix)
    project_id = None

    try:
        args = parser.parse_args(argv)
        project_id = resolve_project_id(args.project)

        if args.command == "list":
            secrets = list_secrets(project_id)
            result = {"project_id": project_id, "secrets": secrets}
            return envelope(ok=True, command=command_str, result=result, next_actions=_next_actions("list", project_id))

        if args.command == "get":
            secret = get_secret(project_id, args.key)
            result = {"project_id": project_id, "secret": secret}
            return envelope(ok=True, command=command_str, result=result, next_actions=_next_actions("get", project_id))

        if args.command == "set":
            secret = set_secret(project_id, args.key, args.value, note=args.note)
            result = {"project_id": project_id, "secret": secret}
            return envelope(ok=True, command=command_str, result=result, next_actions=_next_actions("set", project_id))

        if args.command == "delete":
            delete_secret(project_id, args.key)
            result = {"project_id": project_id, "deleted": args.key}
            return envelope(ok=True, command=command_str, result=result, next_actions=_next_actions("delete", project_id))

    except ArgparseError as exc:
        return envelope(
            ok=False,
            command=command_str,
            error={"message": str(exc), "type": "ArgparseError"},
            fix="Run `carapace bws` for command tree and examples",
            next_actions=_next_actions("error", project_id),
        )
    except FileNotFoundError:
        return envelope(
            ok=False,
            command=command_str,
            error={"message": "bws CLI not found on PATH", "type": "FileNotFoundError"},
            fix="Install bws and ensure it is on PATH",
            next_actions=_next_actions("error", project_id),
        )
    except ValueError as exc:
        return envelope(
            ok=False,
            command=command_str,
            error={"message": str(exc), "type": "ValueError"},
            fix="Ensure the project ID is a valid UUID",
            next_actions=_next_actions("error", project_id),
        )
    except KeyError as exc:
        return envelope(
            ok=False,
            command=command_str,
            error={"message": str(exc), "type": "KeyError"},
            fix=f"Run `carapace bws list {project_id}` to see available secrets",
            next_actions=_next_actions("error", project_id),
        )
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr or exc.stdout or str(exc)
        return envelope(
            ok=False,
            command=command_str,
            error={"message": msg.strip(), "type": "CalledProcessError"},
            fix="Validate bws auth/session and retry",
            next_actions=_next_actions("error", project_id),
        )

        return envelope(ok=False, command=command_str, error={"message": "Unknown command"}, next_actions=_next_actions("error", project_id))


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    response = run_cli(args, command_prefix="carapace-bws")
    print(dump_yaml(response))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
