#!/bin/bash
# =============================================================================
# CFA Factory Book Translator (Parallel)
# =============================================================================
# Translates all readings for a single document (doc_id) in parallel.
# Usage:
#   DOC=OFFICIAL_2026_L1_V3 JOBS=3 ./scripts/translate_book_parallel.sh
#   DOC=OFFICIAL_2026_L1_V3 JOBS=3 FLAGS="--parallel 4" ./scripts/translate_book_parallel.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ -z "${DOC:-}" ]]; then
  echo "DOC is required. Example:"
  echo "  DOC=OFFICIAL_2026_L1_V3 JOBS=3 ./scripts/translate_book_parallel.sh"
  exit 1
fi

JOBS="${JOBS:-2}"
FLAGS="${FLAGS:---parallel 2}"

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

echo "📘 Translating book (parallel): ${DOC}"
echo "Workers: ${JOBS}"
echo "Flags: ${FLAGS}"

printf "%s\n" "${READINGS}" \
  | xargs -P "${JOBS}" -I{} bash -lc \
    'echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━";
     echo "📝 ${DOC} Reading {}";
     run_root="output/runs/${DOC}/{}";
     if [[ ! -d "${run_root}" ]]; then
       echo "⏭️  Skip: missing run directory ${run_root}";
       exit 0;
     fi
     if ! find "${run_root}" -maxdepth 2 -name "english_script.json" -print -quit | grep -q .; then
       echo "⏭️  Skip: no english_script.json under ${run_root}";
       exit 0;
     fi
     uv run cfa translate --doc "${DOC}" --reading "{}" ${FLAGS};
     echo "✅ Done: ${DOC} / {}"'

echo ""
echo "🎉 Completed translations for ${DOC}"
