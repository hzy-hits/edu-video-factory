from __future__ import annotations

from typing import Literal, Optional, List, Dict, Any
from pydantic import BaseModel, Field


SchemaVersion = Literal["v1"]


class Span(BaseModel):
    start_char: int = Field(..., ge=0)
    end_char: int = Field(..., ge=0)
    # 可选：行号定位（后续你要做“引用高亮截图”很有用）
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class Chunk(BaseModel):
    schema_version: SchemaVersion = "v1"

    chunk_id: str
    doc_id: str
    kind: Literal["official", "schweser"]
    page: int = Field(..., ge=1)

    reading_id: Optional[str] = None  # 关键：按 reading 切就必须有
    section_path: List[str] = Field(default_factory=list)

    content_type: Literal["text", "table", "formula", "figure"] = "text"
    content: str
    span: Span
    content_hash: str

    # 便于回放与可追踪
    source_path: Optional[str] = None

    # Phase 0.5: Vision on Demand
    image_ref: Optional[str] = None  # 截图路径
    extracted_struct: Optional[Dict[str, Any]] = None  # 结构化结果 JSON (FormulaAsset/FigureAsset/TableAsset)

    # Phase 0.6: V1 Freeze
    no_cut: bool = False  # Atomic block indicator (Formula/Exhibit/Question zones)
    quiz_ref: Optional[Dict[str, Any]] = None  # Linked quiz metadata

    # Block-level layout metadata (LLM chunker)
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1] in page coordinates
    page_size: Optional[List[float]] = None  # [width, height]
    block_ids: Optional[List[int]] = None  # Text block ids used in this chunk
    image_block_ids: Optional[List[int]] = None  # Image block ids linked to this chunk
    image_bboxes: Optional[List[List[float]]] = None  # Image bboxes for linked images


class RetrievalHit(BaseModel):
    chunk_id: str
    doc_id: str
    page: int
    score: float
    snippet: str


class TopKQuery(BaseModel):
    query: str
    k: int
    hits: List[RetrievalHit]


class EvidencePacket(BaseModel):
    schema_version: SchemaVersion = "v1"

    doc_id: str
    reading_id: str
    run_id: str

    # 本 reading 全文：按 chunk 列表给
    reading_fulltext: List[Chunk]

    # 跨书 top-k 检索结果
    top_k: List[TopKQuery] = Field(default_factory=list)

    # 冲突集：先留空，后续增强
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)

    meta: Dict[str, Any] = Field(default_factory=dict)
