#!/bin/bash
# =============================================================================
# CFA Factory Book Runner
# =============================================================================
# Runs all readings for a single document (doc_id) using reading_map.json.
# Usage:
#   ./scripts/run_book.sh DOC_ID [--no-cross-ref] [--no-editor] [--multi-round|--two-phase|--auto] [--prep]
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ $# -lt 1 ]]; then
  echo "Usage: ./scripts/run_book.sh DOC_ID [--no-cross-ref] [--no-editor] [--multi-round|--two-phase|--auto]"
  exit 1
fi

DOC_ID="$1"
shift

CROSS_REF=1
WITH_EDITOR=1
RUN_FLAGS=()
PREP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-cross-ref)
      CROSS_REF=0
      shift
      ;;
    --no-editor)
      WITH_EDITOR=0
      shift
      ;;
    --multi-round|--two-phase|--auto)
      RUN_FLAGS+=("$1")
      shift
      ;;
    --prep)
      PREP=1
      shift
      ;;
    -h|--help)
      echo "Usage: ./scripts/run_book.sh DOC_ID [--no-cross-ref] [--no-editor] [--multi-round|--two-phase|--auto]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

READINGS=$(python3 - <<PY
import json
doc_id = "${DOC_ID}"
data = json.load(open("assets/reading_map.json", "r", encoding="utf-8"))
readings = data.get(doc_id, [])
print(" ".join(str(r.get("reading_id", "")).strip() for r in readings if str(r.get("reading_id", "")).strip()))
PY
)

if [[ -z "${READINGS}" ]]; then
  echo "No readings found for ${DOC_ID} in assets/reading_map.json"
  exit 1
fi

echo "📘 Running book: ${DOC_ID}"
echo "Readings: ${READINGS}"

for READING_ID in ${READINGS}; do
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📝 Reading: ${READING_ID}"

  RUN_ARGS=("${RUN_FLAGS[@]}")
  if [[ "${CROSS_REF}" -eq 1 ]]; then
    RUN_ARGS+=("--cross-ref")
  fi
  if [[ "${PREP}" -eq 1 ]]; then
    RUN_ARGS+=("--prep")
  fi

  echo "  Running pipeline..."
  if [[ "${WITH_EDITOR}" -eq 1 ]]; then
    uv run cfa run --doc "${DOC_ID}" --reading "${READING_ID}" \
      --with-editor "${RUN_ARGS[@]}"
  else
    uv run cfa run --doc "${DOC_ID}" --reading "${READING_ID}" "${RUN_ARGS[@]}"
  fi

  echo "✅ Done: ${DOC_ID} / ${READING_ID}"
done

echo ""
echo "🎉 Completed all readings for ${DOC_ID}"
