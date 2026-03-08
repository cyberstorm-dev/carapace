import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from carapace.hateoas import dump_yaml, envelope


UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_FIELD_LENGTH = 120
WRAPPER_COMMAND = "carapace-bws"


class ArgparseError(Exception):
    """Raised when CLI arguments are invalid."""


class YamlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgparseError(message)


def resolve_project_id(value: str) -> str:
    if UUID_RE.match(value):
        return value
    raise ValueError(f"Project ID must be a UUID: {value}")


def resolve_project_id_or_default(value: Optional[str]) -> str:
    if value:
        return resolve_project_id(value)
    for key in ("CARAPACE_BWS_PROJECT_ID", "BWS_PROJECT_ID"):
        candidate = os.environ.get(key)
        if candidate:
            return resolve_project_id(candidate)
    raise ValueError("Project ID is required. Provide it as an argument or set CARAPACE_BWS_PROJECT_ID/BWS_PROJECT_ID.")


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


def _same_file(path_a: str, path_b: str) -> bool:
    try:
        return Path(path_a).resolve() == Path(path_b).resolve()
    except OSError:
        return False


def resolve_bws_binary() -> str:
    override = os.environ.get("CARAPACE_BWS_BINARY") or os.environ.get("BWS_BINARY")
    if override:
        return override

    wrapper_path = Path(sys.argv[0]).resolve()
    for candidate in (shutil.which("bws"),):
        if not candidate:
            continue
        if _same_file(candidate, str(wrapper_path)):
            continue
        return candidate

    raise FileNotFoundError("Could not locate a real bws binary. Set CARAPACE_BWS_BINARY to the underlying bws executable.")


def _bws_env() -> Dict[str, str]:
    env = os.environ.copy()
    if "BWS_TOKEN" not in env:
        for key in ("CARAPACE_BWS_TOKEN", "BWS_ACCESS_TOKEN"):
            if os.environ.get(key):
                env["BWS_TOKEN"] = os.environ[key]
                break
    return env


def _parse_json_payload(output: str) -> Optional[Any]:
    try:
        return json.loads(output or "[]")
    except json.JSONDecodeError:
        return None


def run_bws(
    args: List[str],
    input_text: Optional[str] = None,
    binary: Optional[str] = None,
) -> str:
    command = [binary or resolve_bws_binary(), *args]
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        env=_bws_env(),
        check=True,
    )
    return result.stdout


def list_secrets(project_id: str) -> List[Dict[str, Any]]:
    output = run_bws(["secret", "list", project_id])
    items = _parse_json_payload(output) or []
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
        output = run_bws(["secret", "edit", "--value", value, "--note", note, existing["id"]])
        parsed = _parse_json_payload(output)
        if not isinstance(parsed, dict):
            raise ValueError("Unexpected response from bws edit")
        return parse_secret(parsed)

    output = run_bws(["secret", "create", key, value, project_id, "--note", note])
    parsed = _parse_json_payload(output)
    if not isinstance(parsed, dict):
        raise ValueError("Unexpected response from bws create")
    return parse_secret(parsed)


def delete_secret(project_id: str, key: str) -> bool:
    secret = get_secret(project_id, key)
    run_bws(["secret", "delete", secret["id"]])
    return True


def _command_string(argv: List[str], command_prefix: str = WRAPPER_COMMAND) -> str:
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
            {"command": "carapace bws secret list <project_uuid>", "description": "Pass through underlying bws CLI (compat mode)"},
        ],
    }


def _next_actions(command: str, project: Optional[str]) -> List[Dict[str, str]]:
    project = project or "<project_uuid>"
    actions = {
        "list": [
            {"command": f"carapace bws get {project} <key>", "description": "Get a specific secret"},
            {"command": f"carapace bws set {project} <key> <value> --note '<reason>'", "description": "Create or update a secret"},
            {"command": f"carapace bws delete {project} <key>", "description": "Delete a secret"},
            {"command": "bws secret list <project_uuid>", "description": "Run the underlying bws binary directly"},
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
            {"command": "bws", "description": "Access underlying bws command"},
        ],
        "error": [
            {"command": f"carapace bws list {project}", "description": "List secrets for a project"},
            {"command": "carapace bws", "description": "Show command tree"},
        ],
    }
    return actions.get(command, [])


def _proxy_bws_output(argv: List[str], command_prefix: str) -> Dict[str, Any]:
    payload = run_bws(argv)
    parsed = _parse_json_payload(payload)
    result: Dict[str, Any] = {"proxy": "bws", "raw": _protect_text(payload), "command": _command_string(argv, "bws")}
    if parsed is not None:
        result["json"] = parsed
    return envelope(
        command=_command_string(argv, command_prefix),
        ok=True,
        result=result,
        next_actions=[{"command": "carapace bws --help", "description": "Show HATEOAS bws wrapper"}],
    )


def run_cli(argv: List[str], command_prefix: str = WRAPPER_COMMAND) -> Dict[str, Any]:
    if not argv or argv in (["-h"], ["--help"]):
        return envelope(
            command=command_prefix,
            ok=True,
            result=_root_result(),
            next_actions=_next_actions("root", None),
        )

    command_str = _command_string(argv, command_prefix)
    if argv[0] not in {"list", "get", "set", "delete"}:
        return _proxy_bws_output(argv, command_prefix)

    parser = YamlArgumentParser(description="BWS convenience wrapper for carapace", add_help=False)
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("project", nargs="?", default=None)

    get_cmd = sub.add_parser("get")
    get_cmd.add_argument("project", nargs="?", default=None)
    get_cmd.add_argument("key")

    set_cmd = sub.add_parser("set")
    set_cmd.add_argument("project", nargs="?", default=None)
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    set_cmd.add_argument("--note", required=True)

    del_cmd = sub.add_parser("delete")
    del_cmd.add_argument("project", nargs="?", default=None)
    del_cmd.add_argument("key")

    project_id = None
    try:
        args = parser.parse_args(argv)
        project_id = resolve_project_id_or_default(args.project)

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

        return envelope(ok=False, command=command_str, error={"message": "Unknown command"}, next_actions=_next_actions("error", project_id))

    except ArgparseError as exc:
        return envelope(
            ok=False,
            command=command_str,
            error={"message": str(exc), "type": "ArgparseError"},
            fix="Run `carapace bws` for command tree and examples",
            next_actions=_next_actions("error", project_id),
        )
    except (FileNotFoundError, OSError):
        return envelope(
            ok=False,
            command=command_str,
            error={"message": "bws CLI not found on PATH", "type": "FileNotFoundError"},
            fix="Install bws and ensure it is on PATH or set CARAPACE_BWS_BINARY",
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


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    response = run_cli(args, command_prefix="carapace-bws")
    print(dump_yaml(response))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
