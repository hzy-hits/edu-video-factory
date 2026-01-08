from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Literal

import fitz  # PyMuPDF
from loguru import logger
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from cfa_factory.schemas.models import Chunk, Span

load_dotenv()

def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _stable_chunk_id(doc_id: str, page: int, start_char: int, end_char: int, content_hash: str) -> str:
    raw = f"{doc_id}|p{page}|{start_char}-{end_char}|{content_hash}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _find_reading_id(reading_map: Dict[str, List[Dict[str, Any]]], doc_id: str, page: int) -> Optional[str]:
    items = reading_map.get(doc_id, [])
    for it in items:
        if int(it["page_start"]) <= page <= int(it["page_end"]):
            return it["reading_id"]
    return None


class LlmChunkSpec(BaseModel):
    block_ids: List[int]
    image_block_ids: List[int] = Field(default_factory=list)
    content_type: Literal["text", "table", "formula", "figure"]
    section_path: List[str] = Field(default_factory=list)
    no_cut: bool = False


class LlmPageSpec(BaseModel):
    chunks: List[LlmChunkSpec]


def _extract_text_from_block(block: Dict[str, Any]) -> str:
    texts: List[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            t = span.get("text", "").strip()
            if t:
                texts.append(t)
    return " ".join(texts).strip()


def _compute_bbox_union(bboxes: List[List[float]]) -> Optional[List[float]]:
    if not bboxes:
        return None
    x0 = min(b[0] for b in bboxes)
    y0 = min(b[1] for b in bboxes)
    x1 = max(b[2] for b in bboxes)
    y1 = max(b[3] for b in bboxes)
    return [x0, y0, x1, y1]


def _stable_chunk_id_blocks(doc_id: str, page: int, block_ids: List[int], content_hash: str) -> str:
    raw = f"{doc_id}|p{page}|blocks:{','.join(str(i) for i in block_ids)}|{content_hash}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

_thread_local = threading.local()


def _get_genai_client() -> genai.Client:
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = genai.Client()
        _thread_local.client = client
    return client


def _page_to_chunks_llm(
    client: genai.Client,
    model: str,
    doc_id: str,
    kind: str,
    source_path: str,
    page_num_1based: int,
    page_dict: Dict[str, Any],
    page_size: List[float],
    reading_id: Optional[str],
    min_chunk_chars: int = 50,
) -> List[Chunk]:
    text_blocks: List[Dict[str, Any]] = []
    image_blocks: List[Dict[str, Any]] = []
    for block in page_dict.get("blocks", []):
        bbox = block.get("bbox")
        if block.get("type") == 0:
            text = _extract_text_from_block(block)
            if text:
                text_blocks.append({
                    "id": len(text_blocks),
                    "bbox": bbox,
                    "text": text
                })
        elif block.get("type") == 1:
            image_blocks.append({
                "id": len(image_blocks),
                "bbox": bbox
            })

    if not text_blocks and not image_blocks:
        return []

    logger.debug(
        "LLM chunking start p%s model=%s: text_blocks=%s, image_blocks=%s",
        page_num_1based,
        model,
        len(text_blocks),
        len(image_blocks),
    )

    prompt = f"""
You are a PDF block chunker. Given text blocks and image blocks for one page,
group blocks into coherent chunks and label each chunk.

Constraints:
- Each chunk must include at least one text block_id.
- Each text block_id should be used at most once.
- Use only the provided block ids.
- section_path should be a list of section markers (e.g., ["LOS 1.a", "Exhibit 1"]),
  without reading_id (it will be prefixed later).
- Set content_type to one of: text, table, formula, figure.
- Set no_cut=true for formulas, tables, exhibits, questions, solutions, or derivations.

Reading ID: {reading_id or ""}
Page: {page_num_1based}
Page size: {page_size}

Text blocks (id, bbox, text):
{json.dumps(text_blocks, ensure_ascii=False)}

Image blocks (id, bbox):
{json.dumps(image_blocks, ensure_ascii=False)}
"""

    cfg = types.GenerateContentConfig(
        response_schema=LlmPageSpec,
        response_mime_type="application/json",
    )

    try:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=cfg,
        )
        if not resp.parsed:
            raise ValueError("Empty chunking response")
        parsed = resp.parsed
    except Exception as e:
        logger.error(f"LLM chunking failed on page {page_num_1based}: {e}")
        return []

    logger.debug(
        "LLM chunking done p%s model=%s: chunks=%s",
        page_num_1based,
        model,
        len(parsed.chunks),
    )

    chunks: List[Chunk] = []
    for spec in parsed.chunks:
        block_ids = [bid for bid in spec.block_ids if 0 <= bid < len(text_blocks)]
        if not block_ids:
            continue
        text_parts = [text_blocks[i]["text"] for i in block_ids]
        content = "\n".join(t for t in text_parts if t).strip()
        if not content or len(content) < min_chunk_chars:
            continue

        content_hash = _sha1(content)
        chunk_id = _stable_chunk_id_blocks(doc_id, page_num_1based, block_ids, content_hash)

        text_bboxes = [text_blocks[i]["bbox"] for i in block_ids if text_blocks[i].get("bbox")]
        image_ids = [iid for iid in spec.image_block_ids if 0 <= iid < len(image_blocks)]
        image_bboxes = [image_blocks[i]["bbox"] for i in image_ids if image_blocks[i].get("bbox")]
        bbox = _compute_bbox_union(text_bboxes + image_bboxes)

        section_path = [reading_id] if reading_id else []
        for item in spec.section_path:
            if item and item != reading_id:
                section_path.append(item)

        content_type = spec.content_type
        no_cut = spec.no_cut or content_type in ("table", "formula", "figure")

        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                kind=kind,
                page=page_num_1based,
                reading_id=reading_id,
                section_path=section_path,
                content_type=content_type,
                content=content,
                span=Span(start_char=0, end_char=len(content)),
                content_hash=content_hash,
                source_path=source_path,
                no_cut=no_cut,
                bbox=bbox,
                page_size=page_size,
                block_ids=block_ids,
                image_block_ids=image_ids,
                image_bboxes=image_bboxes or None,
            )
        )

    return chunks


def _page_to_chunks(
    doc_id: str,
    kind: str,
    source_path: str,
    page_num_1based: int,
    page_text: str,
    reading_id: Optional[str],
    target_chunk_chars: int = 1800,
    min_chunk_chars: int = 50,  # Skip very short chunks (copyright/headers)
) -> List[Chunk]:
    """
    简化切分：按字符长度分块（工业版可后续替换成段落/文本块级切分）
    经验：1800 chars 约几百 tokens，利于检索。
    Chunks shorter than min_chunk_chars are skipped (copyright pages, headers).
    """
    text = page_text.strip()
    if not text:
        return []

    chunks: List[Chunk] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + target_chunk_chars)
        # 尝试在句号/换行附近截断
        window = text[start:end]
        cut = max(window.rfind("\n"), window.rfind(". "), window.rfind("。"), window.rfind("; "))
        if cut > 500:
            end = start + cut

        content = text[start:end].strip()
        # Skip very short chunks (copyright pages, headers, footers)
        if len(content) < min_chunk_chars:
            start = end
            continue
        if content:
            # V1: Detect coarse section markers for secondary boundaries
            section_marker = None
            marker_patterns = [
                (r"\bLOS\s+\d+(?:\.[a-z])?\b", "LOS"),
                (r"\bLearning Outcome Statement(?:s)?\b", "LOS"),
                (r"\bExample\s+\d+\b", "Example"),
                (r"\bExhibit\s+\d+\b", "Exhibit"),
                (r"\bSummary\b", "Summary"),
                (r"\bKey Concepts\b", "Key Concepts"),
            ]
            for pattern, label in marker_patterns:
                match = re.search(pattern, content, flags=re.IGNORECASE)
                if match:
                    if label in ("Example", "Exhibit", "LOS"):
                        section_marker = match.group(0)
                    else:
                        section_marker = label
                    break

            # V1 Freeze: Heuristics for no_cut zones
            # Detects: Exhibits, Questions, Solutions, Formulas (derivations)
            lower_content = content.lower()
            is_no_cut = False
            triggers = [
                "exhibit", "table ", "figure ", # Visual references
                "question", "solution", "example", # Practice problems
                "step 1", "step 2", "therefore", "derive" # Formula derivations
            ]
            if any(t in lower_content for t in triggers):
                is_no_cut = True
            
            # Special case: Short chunks ending with colon often precede lists/formulas
            if content.endswith(":") and len(content) < 200:
                is_no_cut = True

            content_hash = _sha1(content)
            chunk_id = _stable_chunk_id(doc_id, page_num_1based, start, end, content_hash)
            section_path = [reading_id] if reading_id else []
            if section_marker:
                section_path.append(section_marker)
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    kind=kind,  # official / schweser
                    page=page_num_1based,
                    reading_id=reading_id,
                    section_path=section_path,
                    content_type="text",
                    content=content,
                    span=Span(start_char=start, end_char=end),
                    content_hash=content_hash,
                    source_path=source_path,
                    no_cut=is_no_cut
                )
            )
        start = end

    return chunks


def build_chunks_for_doc(
    pdf_path: Path,
    doc_id: str,
    kind: str,
    reading_map: Dict[str, List[Dict[str, Any]]],
    out_dir: Path,
    target_chunk_chars: int = 1800,
    min_chunk_chars: int = 50,
    use_llm: bool = True,
    llm_model: str = "gemini-2.5-flash-lite",
    llm_model_complex: Optional[str] = "gemini-3-flash-preview",
    parallel: int = 1,
    llm_mode: str = "all",
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{doc_id}.jsonl"

    logger.info(f"Chunking: {pdf_path} -> {out_file}")
    doc = fitz.open(pdf_path)
    n_pages = doc.page_count

    with out_file.open("w", encoding="utf-8") as f:
        total = 0
        page_payloads: List[Dict[str, Any]] = []
        for i in range(n_pages):
            page = doc.load_page(i)
            page_num = i + 1
            text = page.get_text("text") or ""
            reading_id = _find_reading_id(reading_map, doc_id, page_num)
            page_dict = page.get_text("dict") or {}
            page_size = [page.rect.width, page.rect.height]

            drawings = page.get_drawings()
            images = page.get_images()
            needs_vision = len(drawings) > 10 or len(images) > 0 or (0 < len(text) < 200)
            complex_page = needs_vision or bool(
                re.search(r"\b(Exhibit|Table|Figure|Equation|Formula|Derivation|Step\\s+1)\\b", text, flags=re.IGNORECASE)
            )
            if needs_vision:
                logger.debug(f"Page {page_num} flagged for vision (drawings={len(drawings)}, images={len(images)})")

            page_payloads.append({
                "page_num": page_num,
                "text": text,
                "reading_id": reading_id,
                "page_dict": page_dict,
                "page_size": page_size,
                "needs_vision": needs_vision,
                "complex_page": complex_page,
            })

        def _process_page(payload: Dict[str, Any]) -> Tuple[int, List[Chunk]]:
            page_num = payload["page_num"]
            text = payload["text"]
            reading_id = payload["reading_id"]
            page_dict = payload["page_dict"]
            page_size = payload["page_size"]
            needs_vision = payload["needs_vision"]
            complex_page = payload["complex_page"]
            has_blocks = bool(page_dict.get("blocks"))

            page_chunks: List[Chunk] = []
            use_llm_page = use_llm and (
                llm_mode == "all" or (llm_mode == "vision-only" and needs_vision)
            )
            if use_llm_page:
                model_for_page = llm_model_complex if (llm_model_complex and complex_page) else llm_model
                client = _get_genai_client()
                page_chunks = _page_to_chunks_llm(
                    client=client,
                    model=model_for_page,
                    doc_id=doc_id,
                    kind=kind,
                    source_path=str(pdf_path),
                    page_num_1based=page_num,
                    page_dict=page_dict,
                    page_size=page_size,
                    reading_id=reading_id,
                    min_chunk_chars=min_chunk_chars,
                )
                if not page_chunks and has_blocks and model_for_page != llm_model:
                    logger.warning(
                        "LLM chunking fallback p%s: model=%s -> %s",
                        page_num,
                        model_for_page,
                        llm_model,
                    )
                    page_chunks = _page_to_chunks_llm(
                        client=client,
                        model=llm_model,
                        doc_id=doc_id,
                        kind=kind,
                        source_path=str(pdf_path),
                        page_num_1based=page_num,
                        page_dict=page_dict,
                        page_size=page_size,
                        reading_id=reading_id,
                        min_chunk_chars=min_chunk_chars,
                    )

            if not page_chunks:
                page_chunks = _page_to_chunks(
                    doc_id=doc_id,
                    kind=kind,
                    source_path=str(pdf_path),
                    page_num_1based=page_num,
                    page_text=text,
                    reading_id=reading_id,
                    target_chunk_chars=target_chunk_chars,
                    min_chunk_chars=min_chunk_chars,
                )
            return page_num, page_chunks

        if use_llm and parallel > 1:
            results: Dict[int, List[Chunk]] = {}
            with ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = {executor.submit(_process_page, payload): payload["page_num"] for payload in page_payloads}
                for future in as_completed(futures):
                    page_num = futures[future]
                    try:
                        _, page_chunks = future.result()
                    except Exception as e:
                        logger.error(f"Chunking failed on page {page_num}: {e}")
                        page_chunks = []
                    results[page_num] = page_chunks
            for page_num in sorted(results.keys()):
                for ch in results[page_num]:
                    _ = Chunk.model_validate(ch.model_dump())
                    f.write(json.dumps(ch.model_dump(), ensure_ascii=False) + "\n")
                    total += 1
        else:
            for payload in page_payloads:
                _, page_chunks = _process_page(payload)
                for ch in page_chunks:
                    _ = Chunk.model_validate(ch.model_dump())
                    f.write(json.dumps(ch.model_dump(), ensure_ascii=False) + "\n")
                    total += 1

        logger.info(f"Done {doc_id}: pages={n_pages}, chunks={total}")
    return out_file
