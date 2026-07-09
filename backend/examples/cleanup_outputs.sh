#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APPLY=0
INCLUDE_NODE_MODULES=0

usage() {
  cat <<'EOF'
Usage: cleanup_outputs.sh [--apply] [--include-node-modules]

Default mode is dry-run and only prints generated paths that can be removed.
Use --apply to actually delete them.
Use --include-node-modules only if you are happy to reinstall frontend dependencies afterwards.

This script intentionally preserves:
  - backend/configs/thermo_databases
  - benchmark datasets and source code
  - backend/outputs/.gitkeep
EOF
}

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --include-node-modules) INCLUDE_NODE_MODULES=1 ;;
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
  "$ROOT/frontend/dist"
  "$ROOT/.pytest_cache"
  "$ROOT/backend/.pytest_cache"
)

if [[ "$INCLUDE_NODE_MODULES" -eq 1 ]]; then
  targets+=("$ROOT/frontend/node_modules")
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

while IFS= read -r output_path; do
  if [[ "$APPLY" -eq 1 ]]; then
    echo "Removing: $output_path"
    rm -rf "$output_path"
  else
    echo "Would remove: $output_path"
  fi
done < <(
  find "$ROOT/backend/outputs" -mindepth 1 -maxdepth 1 ! -name .gitkeep -print 2>/dev/null | sort
)

while IFS= read -r cache_dir; do
  if [[ "$APPLY" -eq 1 ]]; then
    echo "Removing: $cache_dir"
    rm -rf "$cache_dir"
  else
    echo "Would remove: $cache_dir"
  fi
done < <(
  find \
    "$ROOT/backend/app" \
    "$ROOT/backend/tests" \
    "$ROOT/backend/examples" \
    "$ROOT/backend/benchmarks" \
    -type d -name __pycache__ 2>/dev/null | sort
)

if [[ "$APPLY" -eq 0 ]]; then
  echo
  echo "No files were deleted. Re-run with --apply to remove the listed paths."
fi
