#!/bin/bash
# =============================================================================
# CFA Factory Book Runner (Resume + Translate)
# =============================================================================
# 1) Run only missing readings (no english_script.json)
# 2) Translate readings that lack video_script.json
#
# Usage:
#   ./scripts/run_book_resume_translate.sh OFFICIAL_2026_L1_V3
#   ./scripts/run_book_resume_translate.sh OFFICIAL_2026_L1_V3 -j 3
#   ./scripts/run_book_resume_translate.sh OFFICIAL_2026_L1_V3 \
#     --run-flags "--with-editor --cross-ref --prep --skip-translate" \
#     --translate-flags "--parallel 4"
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

DOC="${DOC:-}"
JOBS="${JOBS:-2}"
RUN_FLAGS="${RUN_FLAGS:---with-editor --cross-ref --prep --skip-translate}"
TRANSLATE_FLAGS="${TRANSLATE_FLAGS:---parallel 2}"
FORCE_TRANSLATE="${FORCE_TRANSLATE:-0}"

usage() {
  echo "Usage:"
  echo "  ./scripts/run_book_resume_translate.sh DOC_ID"
  echo "  ./scripts/run_book_resume_translate.sh DOC_ID -j 3"
  echo "  ./scripts/run_book_resume_translate.sh DOC_ID --run-flags \"...\" --translate-flags \"...\""
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -j|--jobs)
      JOBS="$2"
      shift 2
      ;;
    --run-flags)
      RUN_FLAGS="$2"
      shift 2
      ;;
    --translate-flags)
      TRANSLATE_FLAGS="$2"
      shift 2
      ;;
    --force-translate)
      FORCE_TRANSLATE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "${DOC}" ]]; then
        DOC="$1"
        shift
      else
        echo "Unknown option: $1"
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ -z "${DOC}" ]]; then
  usage
  exit 1
fi

export DOC

echo "DOC: ${DOC}"
echo "Workers: ${JOBS}"
echo "RUN_FLAGS: ${RUN_FLAGS}"
echo "TRANSLATE_FLAGS: ${TRANSLATE_FLAGS}"
echo "FORCE_TRANSLATE: ${FORCE_TRANSLATE}"

MISSING=$(python3 - <<'PY'
import json, os, glob
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

missing = []
for rid in readings:
    run_root = os.path.join("output", "runs", doc, rid)
    if not os.path.isdir(run_root):
        missing.append(rid)
        continue
    if not glob.glob(os.path.join(run_root, "**", "english_script.json"), recursive=True):
        missing.append(rid)

print("\n".join(missing))
PY
)

if [[ -n "${MISSING}" ]]; then
  echo ""
  echo "Missing readings (no english_script.json):"
  echo "${MISSING}"
  printf "%s\n" "${MISSING}" \
    | xargs -P "${JOBS}" -I{} bash -lc \
      'echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━";
       echo "Run missing: ${DOC} Reading {}";
       uv run cfa run --doc "${DOC}" --reading "{}" '"${RUN_FLAGS}"';
       echo "✅ Done (run): ${DOC} / {}"'
else
  echo ""
  echo "No missing readings to run."
fi

TRANSLATE_LIST=$(python3 - <<'PY'
import json, os, glob
doc = os.environ["DOC"]
force = os.environ.get("FORCE_TRANSLATE", "0").strip() in ("1", "true", "yes", "y")
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

needs = []
for rid in readings:
    run_root = os.path.join("output", "runs", doc, rid)
    if not os.path.isdir(run_root):
        continue
    has_en = glob.glob(os.path.join(run_root, "**", "english_script.json"), recursive=True)
    if not has_en:
        continue
    has_zh = glob.glob(os.path.join(run_root, "**", "video_script.json"), recursive=True)
    if force or not has_zh:
        needs.append(rid)

print("\n".join(needs))
PY
)

if [[ -n "${TRANSLATE_LIST}" ]]; then
  echo ""
  echo "Readings to translate:"
  echo "${TRANSLATE_LIST}"
  printf "%s\n" "${TRANSLATE_LIST}" \
    | xargs -P "${JOBS}" -I{} bash -lc \
      'echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━";
       echo "Translate: ${DOC} Reading {}";
       uv run cfa translate --doc "${DOC}" --reading "{}" '"${TRANSLATE_FLAGS}"';
       echo "✅ Done (translate): ${DOC} / {}"'
else
  echo ""
  echo "No readings to translate."
fi

echo ""
echo "🎉 Completed resume + translate for ${DOC}"
