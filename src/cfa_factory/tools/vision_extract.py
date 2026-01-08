from __future__ import annotations

import asyncio
import json
import time
import hashlib
import random
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

import fitz  # PyMuPDF
from google import genai
from google.genai import types
from loguru import logger
from dotenv import load_dotenv

from cfa_factory.schemas.models import Chunk, Span
from cfa_factory.schemas.vision_models import VisionExtractResult, FormulaAsset, FigureAsset, TableAsset


load_dotenv()  # Load environment variables from .env file


def render_page_to_png(pdf_path: Path, page_1based: int, out_png: Path) -> Path:
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_1based - 1)
    pix = page.get_pixmap(dpi=200)  # 200dpi
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_png))
    return out_png


async def extract_page_assets_async(
    client: genai.Client,
    doc_id: str,
    reading_id: str,
    page_1based: int,
    page_png: Path,
    retries: int = 5,
    model: str = "gemini-3-flash-preview",
    formula_only: bool = False,
) -> VisionExtractResult:
    if formula_only:
        prompt = f"""
You are extracting CFA textbook FORMULAS from a single page image.
Return ONLY valid JSON that matches the provided schema.
ALL text in the output MUST be in English.

Tasks:
1) If you see formulas, convert them to LaTeX in `display_latex`, and produce `spoken_en` that is natural for TTS (do NOT read symbols, explain conceptually).
2) If no formulas are present, return assets as an empty list.
3) Every asset must include the page number.

Context: doc_id={doc_id}, reading_id={reading_id}, page={page_1based}.
"""
    else:
        prompt = f"""
You are extracting CFA textbook visuals from a single page image.
Return ONLY valid JSON that matches the provided schema.
ALL text in the output MUST be in English.

Tasks:
1) If you see formulas, convert them to LaTeX in `display_latex`, and produce `spoken_en` that is natural for TTS (do NOT read symbols, explain conceptually).
2) If you see charts/figures, produce `blind_description_en` >= 200 characters describing axes, trends, intersections, and key takeaways in detail.
3) If you see tables, extract them as headers + rows (best-effort; keep cell text).
4) Every asset must include the page number.

Context: doc_id={doc_id}, reading_id={reading_id}, page={page_1based}.
"""

    cfg = types.GenerateContentConfig(
        response_schema=VisionExtractResult,
        response_mime_type="application/json",
    )

    for attempt in range(retries):
        try:
            # Use the async (aio) models client
            resp = await client.aio.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=page_png.read_bytes(), mime_type="image/png"),
                    prompt,
                ],
                config=cfg,
            )
            if not resp.parsed:
                raise ValueError("Empty response from Gemini")
            parsed = resp.parsed
            if formula_only:
                parsed.assets = [a for a in parsed.assets if getattr(a, "type", "") == "formula"]
            return parsed
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait_time = (2 ** attempt) + random.random()
                logger.warning(f"Rate limit hit for page {page_1based}. Retrying in {wait_time:.1f}s... (Attempt {attempt+1}/{retries})")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Error on page {page_1based}: {e}")
                if attempt == retries - 1:
                    raise e
                await asyncio.sleep(1)
    
    raise ValueError(f"Failed after {retries} retries for page {page_1based}")


def _asset_to_chunk(
    doc_id: str,
    reading_id: str,
    source_path: str,
    page: int,
    asset: FormulaAsset | FigureAsset | TableAsset,
    kind: str
) -> Chunk:
    # Construct unique ID
    asset_json = json.dumps(asset.model_dump(), sort_keys=True)
    content_hash = hashlib.sha1(asset_json.encode("utf-8")).hexdigest()
    chunk_id = f"{doc_id}|p{page}|{asset.type}|{content_hash}"
    chunk_id = hashlib.sha1(chunk_id.encode("utf-8")).hexdigest()

    # Searchable text content construction (English)
    if isinstance(asset, FormulaAsset):
        content = f"Formula: {asset.meaning_en} {asset.display_latex}"
    elif isinstance(asset, FigureAsset):
        content = f"Figure: {asset.caption_en} {asset.blind_description_en} {' '.join(asset.key_points)}"
    elif isinstance(asset, TableAsset):
        content = f"Table: {asset.title_en or ''} {' '.join(asset.headers)} " + " | ".join([" ".join(row) for row in asset.rows])
    else:
        content = str(asset)

    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        kind=kind,
        page=page,
        reading_id=reading_id,
        section_path=[reading_id],
        content_type=asset.type,
        content=content,
        span=Span(start_char=0, end_char=0),
        content_hash=content_hash,
        source_path=source_path,
        extracted_struct=asset.model_dump(),
        no_cut=True
    )


def _is_formula_candidate(text: str) -> bool:
    if not text:
        return False
    head = text[:2000]
    if re.search(r"\b(Equation|Formula|Derivation|Step\s+1)\b", head, flags=re.IGNORECASE):
        return True
    symbols = sum(1 for ch in head if ch in "=±×÷√∑∫∂σρμπ^*/")
    ratio = symbols / max(len(head), 1)
    if symbols >= 3 or ratio >= 0.02:
        return True
    if re.search(r"[A-Za-z]\s*=\s*[\dA-Za-z(]", head):
        return True
    return False


async def process_vision_for_reading_async(
    pdf_path: Path,
    doc_id: str,
    reading_id: str,
    pages_range: range,
    chunks_dir: Path,
    assets_out_dir: Path,
    kind: str = "official",
    max_concurrency: int = 10,  # Increased for paid API tier
    model: str = "gemini-3-flash-preview",
    formula_only: bool = False,
    filter_mode: str = "all",
) -> None:
    client = genai.Client()
    semaphore = asyncio.Semaphore(max_concurrency)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunks_file = chunks_dir / f"{doc_id}.jsonl"

    pages = list(pages_range)
    if filter_mode == "formula-pages":
        doc = fitz.open(pdf_path)
        candidate_pages = []
        for p in pages:
            text = doc.load_page(p - 1).get_text("text") or ""
            if _is_formula_candidate(text):
                candidate_pages.append(p)
        doc.close()
        pages = candidate_pages
        logger.info(f"Formula-page filter: {len(pages)} pages selected")

    async def _process_page(p_num: int) -> List[Chunk]:
        async with semaphore:
            # Add a small staggered delay to avoid burst limits
            await asyncio.sleep(0.5 * random.random())
            
            logger.info(f"Processing vision (Async) for {doc_id} R:{reading_id} Page:{p_num}")
            png_path = assets_out_dir / doc_id / f"page_{p_num}.png"
            
            # Rendering
            render_page_to_png(pdf_path, p_num, png_path)
            
            try:
                result = await extract_page_assets_async(
                    client,
                    doc_id,
                    reading_id,
                    p_num,
                    png_path,
                    model=model,
                    formula_only=formula_only
                )
                page_chunks = []
                for asset in result.assets:
                    ch = _asset_to_chunk(doc_id, reading_id, str(pdf_path), p_num, asset, kind)
                    ch.image_ref = str(png_path)
                    page_chunks.append(ch)
                return page_chunks
            except Exception as e:
                logger.error(f"Failed to extract vision for page {p_num} after retries: {e}")
                return []

    tasks = [_process_page(p) for p in pages]
    results = await asyncio.gather(*tasks)
    
    new_chunks = [ch for sublist in results for ch in sublist]

    if new_chunks:
        with chunks_file.open("a", encoding="utf-8") as f:
            for ch in new_chunks:
                f.write(json.dumps(ch.model_dump(), ensure_ascii=False) + "\n")
        logger.info(f"Async processing complete. Appended {len(new_chunks)} vision chunks to {chunks_file}")
