# CFA Video Factory – System Design

## 1. Design Goals
- Evidence-first generation
- Zero hallucination tolerance
- Modular multi-agent architecture
- Cost-controlled long-form output (3–6h per book)

## 2. High-Level Architecture
- Local WSL (indexing, TTS, video)
- Cloud LLMs (logic, language, vision)
- Python Orchestrator (state machine + gates)

## 3. Data Flow
- Phase 0: PDF -> Chunk -> Index
- Phase 0.5: Vision Extract (optional, cached)
- Phase 1: Router -> Config
- Phase 2: Professor / Student -> Claims
- Phase 2.5: Verifier Gate
- Phase 3: Editor (segments)
- Phase 4: Local TTS + MoviePy -> MP4

## 4. Single Source of Truth
VideoScript JSON drives:
- Video
- Audio
- Slides
- Handout PDF

## 5. Failure & Retry Model
- Schema fail -> regenerate same step
- Verifier fail -> retrieve_more or rewrite
- Segment-level retries only

## 6. Scalability
- Reading-level parallelism
- Segment-level retries
- Asset reuse across runs
