#!/bin/bash
# =============================================================================
# CFA Factory Book Runner (Parallel)
# =============================================================================
# Runs all readings for a single document (doc_id) in parallel.
# Usage:
#   DOC=OFFICIAL_2026_L1_V3 JOBS=3 ./scripts/run_book_parallel.sh
#   DOC=OFFICIAL_2026_L1_V3 JOBS=3 FLAGS="--with-editor --cross-ref --prep" ./scripts/run_book_parallel.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ -z "${DOC:-}" ]]; then
  echo "DOC is required. Example:"
  echo "  DOC=OFFICIAL_2026_L1_V3 JOBS=3 ./scripts/run_book_parallel.sh"
  exit 1
fi

JOBS="${JOBS:-2}"
FLAGS="${FLAGS:---with-editor --cross-ref --prep}"

export DOC
export FLAGS

READINGS=$(python3 - <<PY
import json, os
doc = os.environ["DOC"]
data = json.load(open("assets/reading_map.json", "r", encoding="utf-8"))
rows = data.get(doc, [])

def sort_key(r):
    page_start = r.get("page_start")
    try:
        page_start = int(page_start)
    except Exception:
        page_start = 10**9
    rid = str(r.get("reading_id", "")).strip()
    try:
        rid_num = int(rid)
    except Exception:
        rid_num = 10**9
    return (page_start, rid_num)

rows = sorted(rows, key=sort_key)
readings = [str(r.get("reading_id", "")).strip() for r in rows if str(r.get("reading_id", "")).strip()]
print("\n".join(readings))
PY
)

if [[ -z "${READINGS}" ]]; then
  echo "No readings found for ${DOC} in assets/reading_map.json"
  exit 1
fi

echo "📘 Running book (parallel): ${DOC}"
echo "Workers: ${JOBS}"
echo "Flags: ${FLAGS}"

printf "%s\n" "${READINGS}" \
  | xargs -P "${JOBS}" -I{} bash -lc \
    'echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━";
     echo "📝 ${DOC} Reading {}";
     uv run cfa run --doc "${DOC}" --reading "{}" ${FLAGS};
     echo "✅ Done: ${DOC} / {}"'

echo ""
echo "🎉 Completed all readings for ${DOC}"
