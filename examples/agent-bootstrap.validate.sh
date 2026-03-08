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
  if ! grep -Eq '^(ok|command):' "${tmp}"; then
    fail_with_hint "${label}" "Output is not a valid carapace envelope."
  fi
}

echo "Validating carapace-bws command tree..."
if ! carapace-bws --help >"${tmp}" 2>&1; then
  fail_with_hint "carapace-bws --help" "Run examples/agent-bootstrap.sh and check CARAPACE_BWS_TOKEN/PROJECT_ID."
fi
if ! grep -q '^result:' "${tmp}"; then
  fail_with_hint "carapace-bws --help" "Expected envelope result for help response."
fi
echo "PASS: carapace-bws --help"

echo "Validating strict mode: list"
if ! validate_hateoas "carapace-bws list" carapace-bws list; then
  fail_with_hint "carapace-bws list" "Check CARAPACE_BWS_TOKEN and CARAPACE_BWS_PROJECT_ID."
fi
if ! grep -q '^  ok:' "${tmp}" || ! grep -q '^  result:' "${tmp}"; then
  fail_with_hint "carapace-bws list" "Expected HATEOAS envelope with ok/result."
fi
echo "PASS: carapace-bws list"

echo "Validating passthrough mode: carapace-bws secret list"
if ! validate_hateoas "carapace-bws secret list" carapace-bws secret list "${PROJECT_ID}"; then
  fail_with_hint "carapace-bws secret list ${PROJECT_ID}" "Verify CARAPACE_BWS_BINARY points to real bws and auth is valid."
fi
if ! grep -q '^  ok:' "${tmp}" || ! grep -q '^  result:' "${tmp}"; then
  fail_with_hint "carapace-bws secret list" "Passthrough output is not a valid HATEOAS envelope."
fi
if ! grep -q '^  result:' "${tmp}"; then
  fail_with_hint "carapace-bws secret list" "Proxy command did not return expected envelope body."
fi
echo "PASS: carapace-bws secret list ${PROJECT_ID}"

echo "Bootstrap validation completed."
