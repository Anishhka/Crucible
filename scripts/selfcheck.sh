#!/usr/bin/env bash
#
# Conformance self-check. Run this from the root of your implementation.
#
#   .../scripts/selfcheck.sh
#   .../scripts/selfcheck.sh --skip-build --image crucible:dev
#   .../scripts/selfcheck.sh --only cli,determinism
#
# Requires: docker, python3 >= 3.11, and `jsonschema` importable (pip install jsonschema).
# That dependency belongs to this script, not to your implementation.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 2
fi

exec python3 "${HERE}/selfcheck.py" --repo "$(pwd)" "$@"
