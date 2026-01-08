# Technical Architecture

## Runtime
- OS: WSL2 (Ubuntu)
- Python 3.11+
- Package Manager: uv

## Storage
- JSONL for chunks
- Chroma for vector index
- File-based run directories

## LLM Access
- Gemini Developer API (AI Studio key)
- DeepSeek API for long Chinese output

## Vision
- Page-level PNG rendering via PyMuPDF
- Gemini multimodal extraction

## Audio
- GPT-SoVITS (local GPU)

## Video
- MoviePy for orchestration
- ffmpeg for encoding

## Validation
- Pydantic schemas
- Hard schema gates at every stage
