#!/bin/bash
# =============================================================================
# CFA Factory Batch Processing Script
# =============================================================================
# Processes all documents in the manifest through the full pipeline.
# Usage: ./scripts/batch_all.sh [--multi-round]
# =============================================================================

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Parse arguments
MULTI_ROUND=""
if [[ "$1" == "--multi-round" ]]; then
    MULTI_ROUND="--multi-round"
    echo "📚 Multi-round debate mode enabled (2-4 hour video content)"
fi

echo "🏭 CFA Factory Batch Processing"
echo "================================"
echo ""

# Get all doc_ids from manifest
DOC_IDS=$(cat assets/manifest.json | python3 -c "import sys,json; docs=json.load(sys.stdin)['documents']; print(' '.join(d['doc_id'] for d in docs))")

for DOC_ID in $DOC_IDS; do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📖 Processing: $DOC_ID"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Step 1: Chunk the PDF
    echo "  [1/4] Chunking PDF..."
    uv run cfa chunk --doc "$DOC_ID" 2>&1 | tail -3
    
    # Step 2: Build index
    echo "  [2/4] Building vector index..."
    uv run cfa index --doc "$DOC_ID" 2>&1 | tail -3
    
    # Step 3: Get readings for this document
    READINGS=$(cat assets/reading_map.json | python3 -c "import sys,json; data=json.load(sys.stdin); readings=data.get('$DOC_ID', []); print(' '.join(r['reading_id'] for r in readings))" 2>/dev/null || echo "")
    
    if [[ -z "$READINGS" ]]; then
        echo "  ⚠️ No readings defined in reading_map.json for $DOC_ID, skipping..."
        continue
    fi
    
    for READING_ID in $READINGS; do
        echo ""
        echo "  📝 Reading: $READING_ID"
        
        # Step 3a: Generate evidence packet
        echo "    [3/4] Generating evidence packet..."
        uv run cfa packet --doc "$DOC_ID" --reading "$READING_ID" 2>&1 | tail -2
        
        # Step 4: Run debate pipeline
        echo "    [4/4] Running debate pipeline..."
        uv run cfa run --doc "$DOC_ID" --reading "$READING_ID" $MULTI_ROUND 2>&1 | tail -5
        
        echo "    ✅ Done: $DOC_ID / $READING_ID"
    done
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 Batch processing complete!"
echo "   Output: output/runs/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
