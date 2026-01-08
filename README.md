# CFA Factory

AI-Powered CFA Study Video Factory - Transform CFA curriculum into educational video scripts using multi-agent debate.

## Overview

CFA Factory uses a multi-agent debate system (Professor vs Student) to deeply understand CFA materials and generate educational content. The system is built on Google's Agent Development Kit (ADK).

## Architecture

### Data Flow

```
PDF (Official/Schweser)
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│                     Preprocessing                              │
│  chunk ──► index ──► evidence_packet                          │
│  (PyMuPDF)  (ChromaDB)  (retrieval)                           │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│                     Debate Pipeline                            │
│                                                                │
│  Router ─► TA Outline ─► Search ─► Professor ─► Student       │
│                                        │           │          │
│                                        ▼           ▼          │
│                                    Synthesis ◄────────        │
│                                        │                      │
│                                        ▼                      │
│                                    Verifier                   │
│                                                                │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│                   Script Generation                            │
│                                                                │
│  Lecture Draft ─► Dialogue Expander ─► Translator ─► Output   │
│  (Gemini Pro)    (Gemini Pro)          (DeepSeek)             │
│                                                                │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
   video_script.json / script_zh.txt
```

### Agent Roles

| Agent | Model | Role |
|-------|-------|------|
| Router | Gemini Flash | Classify reading into pedagogical mode |
| TA Outline | Gemini Flash | Build lecture outline with key questions |
| Search | Gemini Flash | Web search for real-world examples |
| Professor | Gemini Pro | Generate first-principles claims with citations |
| Student | Gemini Flash | Challenge claims with counter-examples |
| Synthesis | Gemini Pro | Reconcile claims and challenges |
| Verifier | Gemini Flash | Audit claims against evidence |
| Lecture Drafter | Gemini Pro | Convert claims to dialogue draft |
| Dialogue Expander | Gemini Pro | Expand draft into multi-turn dialogue |
| Translator | DeepSeek | Translate English script to Chinese |

### Pipeline Modes

| Mode | Flag | Description |
|------|------|-------------|
| Debate only | (default) | Router → Professor → Student → Synthesis → Verifier |
| Multi-round | `--multi-round` | Adds deep-dive round before Verifier |
| Production | `--with-editor` | Full pipeline with script generation |
| English-only | `--skip-translate` | Production without translation step |
| Two-phase | `--two-phase` | Outline → Scene expansion loop → Translation |

## Installation

```bash
git clone https://github.com/yourusername/cfa_factory.git
cd cfa_factory
uv sync

cp .env.example .env
# Edit .env with your API keys:
# - GEMINI_API_KEY (required)
# - DEEPSEEK_API_KEY (optional, for translation)
```

## Quick Start

```bash
# Step 1: Chunk the PDF (one time per book)
uv run cfa chunk --doc OFFICIAL_2026_L1_V9 --llm

# Step 2: Build vector index (one time per book)
uv run cfa index --doc OFFICIAL_2026_L1_V9

# Step 3: Run pipeline for a reading
uv run cfa run --doc OFFICIAL_2026_L1_V9 --reading 1 --with-editor --cross-ref --prep

# English-only (skip translation)
uv run cfa run --doc OFFICIAL_2026_L1_V9 --reading 1 --with-editor --prep --skip-translate

# Translate existing English script
uv run cfa translate --doc OFFICIAL_2026_L1_V9 --reading 1
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `cfa chunk --doc DOC_ID` | Extract text chunks from PDF |
| `cfa index --doc DOC_ID` | Build ChromaDB vector index |
| `cfa index-all` | Build unified cross-book index |
| `cfa packet --doc DOC_ID --reading 1` | Generate evidence packet |
| `cfa run --doc DOC_ID --reading 1 --with-editor` | Run full pipeline |
| `cfa translate --doc DOC_ID --reading 1` | Translate existing English script |
| `cfa vision extract --doc DOC_ID --reading 1` | Extract figures/formulas |

## Document IDs

### Official CFA Materials
| doc_id | Volume |
|--------|--------|
| `OFFICIAL_2026_L1_V1` | Quantitative Methods |
| `OFFICIAL_2026_L1_V2` | Economics |
| `OFFICIAL_2026_L1_V3` | Corporate Issuers |
| `OFFICIAL_2026_L1_V4` | Financial Statement Analysis |
| `OFFICIAL_2026_L1_V5` | Equity Investments |
| `OFFICIAL_2026_L1_V6` | Fixed Income |
| `OFFICIAL_2026_L1_V7` | Derivatives |
| `OFFICIAL_2026_L1_V8` | Alternative Investments |
| `OFFICIAL_2026_L1_V9` | Portfolio Management |
| `OFFICIAL_2026_L1_V10` | Ethics |

### Schweser Notes
| doc_id | Content |
|--------|---------|
| `SCHWESER_2026_L1_B1` | Quant, Economics, Corporate Issuers |
| `SCHWESER_2026_L1_B2` | FSA, Equity |
| `SCHWESER_2026_L1_B3` | Fixed Income, Derivatives |
| `SCHWESER_2026_L1_B4` | Alts, PM, Ethics |

## Batch Processing

Process an entire book:

```bash
./scripts/run_book.sh OFFICIAL_2026_L1_V3 --prep
```

Parallel execution:

```bash
DOC=OFFICIAL_2026_L1_V3 JOBS=3 ./scripts/run_book_parallel.sh
```

English-first workflow (generate English, then translate in parallel):

```bash
DOC=OFFICIAL_2026_L1_V3 JOBS=3 FLAGS="--with-editor --prep --skip-translate" ./scripts/run_book_parallel.sh
DOC=OFFICIAL_2026_L1_V3 JOBS=3 ./scripts/translate_book_parallel.sh
```

## Output Structure

```
output/
├── index/
│   ├── chunks/                     # Text chunks (JSONL)
│   │   └── OFFICIAL_2026_L1_V9.jsonl
│   └── chroma/                     # Vector indices
│       ├── OFFICIAL_2026_L1_V9/
│       └── unified/                # Cross-book index
├── runs/
│   └── OFFICIAL_2026_L1_V9/
│       └── 1/                      # reading_id
│           └── 20260105T001101/    # run timestamp
│               ├── evidence_packet.json
│               ├── state.json
│               ├── professor_lecture.json
│               ├── english_script.json
│               ├── video_script.json
│               └── script_zh.txt
└── vision_assets/                  # Extracted figures/formulas
```

## Philosophical Lenses

The debate uses explicit epistemological frameworks:

**Professor (Thesis):**
- First Principles derivation
- Dialectical Synthesis
- Systems Dynamics
- Signaling Theory

**Student (Antithesis):**
- Popperian Falsification
- Fat Tail Analysis
- Inversion Testing
- Fragility Probing

## Configuration

Defaults in `config/cfa.yaml`:

```yaml
min_claims: 80
min_scenes: 90
min_scene_words: 90
smooth_zh: true
smooth_window: 6
```

Override with environment variable:

```bash
export CFA_CONFIG=/path/to/custom.yaml
```

## Tech Stack

- Framework: Google ADK
- Models: Gemini 3 Pro/Flash, DeepSeek Chat
- Vector Store: ChromaDB
- Embeddings: text-embedding-004
- PDF: PyMuPDF

## License

MIT
