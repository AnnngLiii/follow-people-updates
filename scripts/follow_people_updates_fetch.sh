#!/usr/bin/env bash
set -euo pipefail

# Run the workspace fetcher without relying on user-specific absolute paths.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SCRIPT_PATH="$REPO_ROOT/skills/follow-people-updates/scripts/fetch_updates.py"
DEFAULT_REGISTRY="$REPO_ROOT/skills/follow-people-updates/assets/tracking-registry.json"

if [[ ! -f "$SCRIPT_PATH" ]]; then
  echo "Missing fetcher script: $SCRIPT_PATH" >&2
  exit 1
fi

export FOLLOW_PEOPLE_UPDATES_REGISTRY="${FOLLOW_PEOPLE_UPDATES_REGISTRY:-$DEFAULT_REGISTRY}"
export FOLLOW_PEOPLE_UPDATES_REQUEST_TIMEOUT="${FOLLOW_PEOPLE_UPDATES_REQUEST_TIMEOUT:-8}"
export FOLLOW_PEOPLE_UPDATES_SOURCE_TIMEOUT="${FOLLOW_PEOPLE_UPDATES_SOURCE_TIMEOUT:-12}"

exec python3 "$SCRIPT_PATH" "$@"
