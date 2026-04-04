#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPLY=0
INCLUDE_VENV=0

usage() {
  cat <<'EOF'
Usage: cleanup-generated.sh [--apply] [--include-venv]

Default mode is dry-run and prints the generated directories that can be removed.
Use --apply to delete reproducible artifacts.
Use --include-venv only if you are happy to recreate backend/.venv afterwards.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --include-venv) INCLUDE_VENV=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

targets=(
  "$ROOT/frontend/node_modules"
  "$ROOT/frontend/dist"
  "$ROOT/frontend/node_modules"
  "$ROOT/frontend/dist"
  "$ROOT/outputs"
  "$ROOT/src/__pycache__"
  "$ROOT/src/Multi_agents/__pycache__"
  "$ROOT/src/config/__pycache__"
  "$ROOT/src/graphs/__pycache__"
  "$ROOT/src/reasoning/__pycache__"
  "$ROOT/src/schemas/__pycache__"
  "$ROOT/src/tools/__pycache__"
  "$ROOT/src/utils/__pycache__"
  "$ROOT/frontend/src/__pycache__"
  "$ROOT/frontend/src/__pycache__"
)

if [[ "$INCLUDE_VENV" -eq 1 ]]; then
  targets+=("$ROOT/backend/.venv")
fi

echo "Repository root: $ROOT"
echo "Mode: $([[ "$APPLY" -eq 1 ]] && echo apply || echo dry-run)"
echo

for target in "${targets[@]}"; do
  if [[ -e "$target" ]]; then
    if [[ "$APPLY" -eq 1 ]]; then
      echo "Removing: $target"
      rm -rf "$target"
    else
      echo "Would remove: $target"
    fi
  else
    echo "Missing: $target"
  fi
done

if [[ "$APPLY" -eq 0 ]]; then
  echo
  echo "No files were deleted. Re-run with --apply to remove the listed paths."
fi
