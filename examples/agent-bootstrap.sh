#!/usr/bin/env bash

set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Run this with: source $(basename "$0")"
  echo "Then rerun the script after sourcing to keep the bws alias in your shell."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${CARAPACE_REPO_ROOT:-${SCRIPT_DIR}/..}"

VENV_PATH="${CARAPACE_VENV:-}"
if [[ -z "${VENV_PATH}" ]]; then
  if [[ -n "${OPENCLOW_VENV:-}" && -f "${OPENCLOW_VENV}" ]]; then
    VENV_PATH="${OPENCLOW_VENV}"
  elif [[ -f "${HOME}/.openclaw/venv/bin/activate" ]]; then
    VENV_PATH="${HOME}/.openclaw/venv/bin/activate"
  elif [[ -f "/Users/openclaw/.openclaw/venv/bin/activate" ]]; then
    VENV_PATH="/Users/openclaw/.openclaw/venv/bin/activate"
  fi
fi

if [[ -z "${VENV_PATH}" || ! -f "${VENV_PATH}" ]]; then
  echo "Virtualenv activation file not found: ${VENV_PATH:-<unset>}"
  echo "Set CARAPACE_VENV (or OPENCLOW_VENV) to a valid venv path and retry."
  return 1
fi

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "Carapace checkout not found: ${REPO_ROOT}"
  echo "Set CARAPACE_REPO_ROOT to the correct checkout and retry."
  return 1
fi

if [[ -z "${CARAPACE_BWS_TOKEN:-}" && -n "${BWS_TOKEN:-}" ]]; then
  export CARAPACE_BWS_TOKEN="${BWS_TOKEN}"
fi
if [[ -z "${CARAPACE_BWS_PROJECT_ID:-}" ]]; then
  echo "Set CARAPACE_BWS_PROJECT_ID (project UUID) before sourcing."
  return 1
fi
if [[ -z "${CARAPACE_BWS_TOKEN:-}" ]]; then
  echo "Set CARAPACE_BWS_TOKEN or BWS_TOKEN before sourcing."
  return 1
fi

source "${VENV_PATH}"
cd "${REPO_ROOT}"
python -m pip install -e .

if [[ -z "${CARAPACE_BWS_BINARY:-}" ]]; then
  if command -v bws >/dev/null 2>&1; then
    export CARAPACE_BWS_BINARY="$(command -v bws)"
  else
    echo "Could not auto-detect underlying bws. Set CARAPACE_BWS_BINARY explicitly."
    return 1
  fi
fi

alias bws='carapace-bws'
export BWS_TOKEN="${CARAPACE_BWS_TOKEN}"

echo "Carapace agent bootstrap complete."
echo "Validation:"
echo "- carapace-bws --help"
echo "- carapace-bws list"
