#!/usr/bin/env bash

set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Run this with: source $(basename "$0")"
  echo "or execute from an already-bootstrapped shell."
  exit 1
fi

PROJECT_ID="${CARAPACE_BWS_PROJECT_ID:-}"
if [[ -z "${PROJECT_ID}" ]]; then
  echo "Missing CARAPACE_BWS_PROJECT_ID"
  echo "Set CARAPACE_BWS_PROJECT_ID before validating."
  return 1
fi

if ! command -v carapace-bws >/dev/null 2>&1; then
  echo "carapace-bws not found on PATH."
  echo "Run examples/agent-bootstrap.sh first, or activate your env."
  return 1
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  PYTHON_BIN=""
fi
PYTHON_YAML_CHECK=0
if [[ -n "${PYTHON_BIN}" ]]; then
  if "${PYTHON_BIN}" - <<'PY'
import yaml  # noqa: F401
PY
  then
    PYTHON_YAML_CHECK=1
  else
    PYTHON_YAML_CHECK=0
  fi
fi

tmp="$(mktemp)"
cleanup() { rm -f "${tmp}"*; }
trap cleanup EXIT

fail_with_hint() {
  echo "Validation failed at: $1"
  echo "Hint: $2"
  return 1
}

validate_hateoas() {
  local label="$1"
  shift
  "$@" >"${tmp}" 2>&1 || fail_with_hint "${label}" "Command failed. stdout/stderr:"
  if [[ "${PYTHON_YAML_CHECK}" == "1" && -n "${PYTHON_BIN}" ]]; then
    if ! "${PYTHON_BIN}" - "$tmp" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fp:
    data = yaml.safe_load(fp.read())

if not isinstance(data, dict):
    raise SystemExit("Invalid payload root")
if "command" not in data or "ok" not in data:
    raise SystemExit("Missing command/ok keys")
if not isinstance(data["ok"], bool):
    raise SystemExit("Invalid ok key")
if data["ok"] and "result" not in data:
    raise SystemExit("Missing result on success payload")
if (not data["ok"]) and "error" not in data:
    raise SystemExit("Missing error on failure payload")
PY
    then
      fail_with_hint "${label}" "Output is not a valid carapace envelope."
    fi
  else
    if ! grep -Eq '^(ok|command):' "${tmp}"; then
      fail_with_hint "${label}" "Output is not a valid carapace envelope."
    fi
  fi
}

echo "Validating carapace-bws command tree..."
if ! validate_hateoas "carapace-bws --help" carapace-bws --help; then
  fail_with_hint "carapace-bws --help" "Output not a valid envelope."
fi
echo "PASS: carapace-bws --help"

echo "Validating strict mode: list"
if ! validate_hateoas "carapace-bws list" carapace-bws list; then
  fail_with_hint "carapace-bws list" "Check CARAPACE_BWS_TOKEN and CARAPACE_BWS_PROJECT_ID."
fi
echo "PASS: carapace-bws list"

echo "Validating passthrough mode: carapace-bws secret list"
if ! validate_hateoas "carapace-bws secret list" carapace-bws secret list "${PROJECT_ID}"; then
  fail_with_hint "carapace-bws secret list ${PROJECT_ID}" "Verify CARAPACE_BWS_BINARY points to real bws and auth is valid."
fi
echo "PASS: carapace-bws secret list ${PROJECT_ID}"

echo "Bootstrap validation completed."
