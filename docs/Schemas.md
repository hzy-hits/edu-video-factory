# Schemas

## Chunk.schema.json
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Chunk",
  "type": "object",
  "required": [
    "schema_version",
    "chunk_id",
    "doc_id",
    "kind",
    "page",
    "content_type",
    "content",
    "content_hash"
  ],
  "properties": {
    "schema_version": { "const": "v1" },
    "chunk_id": { "type": "string" },
    "doc_id": { "type": "string" },
    "kind": { "enum": ["official", "schweser"] },
    "page": { "type": "integer", "minimum": 1 },
    "reading_id": { "type": ["string", "null"] },
    "section_path": {
      "type": "array",
      "items": { "type": "string" }
    },
    "no_cut": { "type": "boolean" },
    "content_type": {
      "enum": ["text", "table", "formula", "figure"]
    },
    "content": { "type": "string" },
    "span": {
      "type": ["object", "null"],
      "properties": {
        "start_char": { "type": "integer" },
        "end_char": { "type": "integer" },
        "start_line": { "type": ["integer", "null"] },
        "end_line": { "type": ["integer", "null"] }
      }
    },
    "content_hash": { "type": "string" },
    "source_path": { "type": ["string", "null"] },
    "image_ref": { "type": ["string", "null"] },
    "extracted_struct": {
      "type": ["object", "null"],
      "description": "Structured multimodal extraction (table/formula/figure)"
    },
    "bbox": {
      "type": ["array", "null"],
      "items": { "type": "number" },
      "description": "Chunk bounding box [x0,y0,x1,y1]"
    },
    "page_size": {
      "type": ["array", "null"],
      "items": { "type": "number" },
      "description": "Page size [width,height]"
    },
    "block_ids": {
      "type": ["array", "null"],
      "items": { "type": "integer" }
    },
    "image_block_ids": {
      "type": ["array", "null"],
      "items": { "type": "integer" }
    },
    "image_bboxes": {
      "type": ["array", "null"],
      "items": {
        "type": "array",
        "items": { "type": "number" }
      }
    }
  }
}
```

## EvidencePacket.schema.json
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "EvidencePacket",
  "type": "object",
  "required": [
    "schema_version",
    "doc_id",
    "reading_id",
    "run_id",
    "reading_fulltext"
  ],
  "properties": {
    "schema_version": { "const": "v1" },
    "doc_id": { "type": "string" },
    "reading_id": { "type": "string" },
    "run_id": { "type": "string" },
    "reading_fulltext": {
      "type": "array",
      "items": { "$ref": "Chunk.schema.json" }
    },
    "top_k": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["query", "k", "hits"],
        "properties": {
          "query": { "type": "string" },
          "k": { "type": "integer" },
          "hits": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["chunk_id", "doc_id", "page", "score", "snippet"],
              "properties": {
                "chunk_id": { "type": "string" },
                "doc_id": { "type": "string" },
                "page": { "type": "integer" },
                "score": { "type": "number" },
                "snippet": { "type": "string" }
              }
            }
          }
        }
      }
    },
    "conflicts": {
      "type": "array",
      "items": { "type": "object" }
    },
    "meta": { "type": "object" }
  }
}
```

## VerifierOutput.schema.json
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "VerifierOutput",
  "type": "object",
  "required": ["verdicts", "overall_decision"],
  "properties": {
    "verdicts": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["claim_id", "status"],
        "properties": {
          "claim_id": { "type": "string" },
          "status": { "enum": ["PASS", "WEAK", "HALLUCINATION"] },
          "reason": { "type": "string" },
          "fix_suggestion": { "type": ["string", "null"] }
        }
      }
    },
    "overall_decision": {
      "enum": ["PROCEED", "RETRIEVE_MORE", "REWRITE"]
    }
  }
}
```

## VideoScript.schema.json
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "VideoScript",
  "type": "object",
  "required": ["segment_id", "scenes"],
  "properties": {
    "segment_id": { "type": "string" },
    "duration_est_min": { "type": ["integer", "null"], "minimum": 0 },
    "scenes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["beat", "display_zh", "spoken_zh", "speaker"],
        "properties": {
          "beat": {
            "enum": [
              "misconception",
              "first_principles",
              "numeric_example",
              "exam_trap",
              "synthesis",
              "quiz"
            ]
          },
          "speaker": { "enum": ["Professor", "Student", "Narrator"] },
          "display_zh": { "type": "string" },
          "spoken_zh": { "type": "string" },
          "citations": {
            "type": "array",
            "items": { "type": "string" }
          },
          "visual_refs": {
            "type": "array",
            "items": { "type": "string" }
          },
          "quiz": {
            "type": ["object", "null"],
            "properties": {
              "type": { "enum": ["MCQ", "TF"] },
              "question_zh": { "type": "string" },
              "choices": {
                "type": ["array", "null"],
                "items": { "type": "string" },
                "minItems": 2
              },
              "answer": { "type": ["string", "boolean"] },
              "explanation_zh": { "type": "string" },
              "answer_citations": {
                "type": "array",
                "items": { "type": "string" }
              }
            }
          }
        }
      }
    }
  }
}
```

## LessonSummary.schema.json
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "LessonSummary",
  "type": "object",
  "required": ["lesson_id", "key_takeaways", "formulas_used", "exhibits_used", "open_loops"],
  "properties": {
    "lesson_id": { "type": "string" },
    "key_takeaways": {
      "type": "array",
      "items": { "type": "string" }
    },
    "formulas_used": {
      "type": "array",
      "items": { "type": "string" }
    },
    "exhibits_used": {
      "type": "array",
      "items": { "type": "string" }
    },
    "open_loops": {
      "type": "array",
      "items": { "type": "string" }
    }
  }
}
```

## SearchContext.schema.json
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SearchContext",
  "type": "object",
  "required": ["query", "sources"],
  "properties": {
    "query": { "type": "string" },
    "sources": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["title", "url", "summary"],
        "properties": {
          "title": { "type": "string" },
          "url": { "type": "string" },
          "summary": { "type": "string" }
        }
      }
    }
  }
}
```
