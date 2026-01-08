from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

import fitz  # PyMuPDF
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel
from google import genai
from google.genai import types

from cfa_factory.tools.manifest import load_manifest

load_dotenv()


class ReadingStartSchema(BaseModel):
    is_reading_start: bool
    reading_id: Optional[str] = None
    reading_title: Optional[str] = None


class TocMapEntry(BaseModel):
    reading_id: Optional[str] = None
    reading_title: str
    page_start: Optional[int] = None


class TocMapSchema(BaseModel):
    entries: List[TocMapEntry]


def _is_candidate_page(text: str, text_dict: Dict[str, Any]) -> bool:
    head = text[:2000]
    if re.search(r"\bContents\b", head, flags=re.IGNORECASE):
        return False
    if re.search(r"\bLearning\s+Module\s+Overview\b", head, flags=re.IGNORECASE):
        return True
    if re.search(r"\bLearning\s+Outcomes\b", head, flags=re.IGNORECASE):
        return True
    if not re.search(r"\b(Reading|Learning\s+Module)\s+\d+\b", head, flags=re.IGNORECASE):
        return False
    if not re.search(r"\b(Introduction|LOS|Learning\s+Outcomes)\b", head, flags=re.IGNORECASE):
        return False
    return True


def _confirm_reading_start(client: genai.Client, model: str, page_text: str) -> ReadingStartSchema:
    prompt = """
You are detecting whether a PDF page is the START of a CFA Reading or Learning Module.
If it is a reading start, extract:
- reading_id as a plain number string (e.g., "1", "2", ...)
- reading_title (English)
If not, set is_reading_start=false.

Return ONLY JSON matching ReadingStartSchema.
"""
    cfg = types.GenerateContentConfig(
        response_schema=ReadingStartSchema,
        response_mime_type="application/json",
    )
    resp = client.models.generate_content(
        model=model,
        contents=[prompt, page_text[:2000]],
        config=cfg,
    )
    if not resp.parsed:
        raise ValueError("Empty response from Gemini")
    return resp.parsed


def _extract_toc_map_from_pages(
    client: genai.Client,
    model: str,
    pages: List[Dict[str, Any]]
) -> TocMapSchema:
    prompt = """
You are given the first N PDF pages (each labeled as PAGE <number>).
Some pages are Table of Contents.
Extract the list of CFA Readings / Learning Modules with their START PAGE.

Rules:
- Output page_start as the PDF PAGE number (the numeric label in the input).
- If only TOC page numbers are visible, infer a constant offset from any observed start pages.
- If you cannot determine a page_start, set it to null.

Return ONLY JSON matching TocMapSchema.
"""
    contents: List[Any] = [prompt]
    for item in pages:
        contents.append(f"PAGE {item['page_num']}:\n{item['text']}")
    cfg = types.GenerateContentConfig(
        response_schema=TocMapSchema,
        response_mime_type="application/json",
    )
    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config=cfg,
    )
    if not resp.parsed:
        raise ValueError("Empty response from Gemini")
    return resp.parsed
    prompt = """
You are given the TABLE OF CONTENTS pages for a CFA book (multiple pages).
Extract ONLY the Learning Module / Reading entries.
For each entry, return:
- reading_id as a plain number string (e.g., "1", "2", ...) if present in the TOC, else null
- reading_title (English, as in TOC)
- page_start as an integer TOC page number if present, else null

Return ONLY JSON matching TocSchema.
"""
    cfg = types.GenerateContentConfig(
        response_schema=TocSchema,
        response_mime_type="application/json",
    )
    resp = client.models.generate_content(
        model=model,
        contents=[prompt, toc_text[:12000]],
        config=cfg,
    )
    if not resp.parsed:
        raise ValueError("Empty response from Gemini")
    return resp.parsed


def _normalize_reading_id(value: Optional[str], fallback_idx: int) -> str:
    if value:
        raw = value.strip()
        match = re.search(r"\bR\s*(\d+)\b", raw, flags=re.IGNORECASE)
        if match:
            return str(int(match.group(1)))
        match = re.search(r"\bLearning\s+Module\s*(\d+)\b", raw, flags=re.IGNORECASE)
        if match:
            return str(int(match.group(1)))
        match = re.search(r"\bReading\s*(\d+)\b", raw, flags=re.IGNORECASE)
        if match:
            return str(int(match.group(1)))
        if raw.isdigit():
            return str(int(raw))
    return str(fallback_idx)


def _normalize_toc_entries(
    entries: List[TocMapEntry],
    n_pages: int,
    debug: bool = False
) -> List[Dict[str, Any]]:
    reading_starts: List[Dict[str, Any]] = []
    for idx, entry in enumerate(entries):
        title = entry.reading_title.strip()
        if not title:
            continue
        page_start = entry.page_start
        if page_start is not None and (page_start < 1 or page_start > n_pages):
            if debug:
                logger.warning(f"Skipping out-of-range page {page_start} for {title}")
            page_start = None
        reading_starts.append({
            "reading_id": _normalize_reading_id(entry.reading_id, len(reading_starts) + 1),
            "reading_title": title,
            "page_start": page_start,
        })
    return [r for r in reading_starts if r.get("page_start")]


def build_reading_map_for_doc(
    doc_id: str,
    manifest_path: Path,
    out_path: Path,
    use_llm: bool = True,
    llm_model: str = "gemini-3-flash-preview",
    max_pages: Optional[int] = None,
    toc_pages: int = 15,
    debug: bool = False,
) -> Dict[str, Any]:
    manifest = load_manifest(manifest_path)
    doc_entry = next((d for d in manifest if d.doc_id == doc_id), None)
    if not doc_entry:
        raise ValueError(f"doc_id={doc_id} not in manifest")

    pdf_path = Path(manifest_path).parent / doc_entry.path
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    logger.info(f"Building reading_map for {doc_id} via {'LLM' if use_llm else 'regex'}")
    doc = fitz.open(pdf_path)
    n_pages = doc.page_count
    scan_pages = min(n_pages, max_pages) if max_pages else n_pages

    client = genai.Client() if use_llm else None
    reading_starts: List[Dict[str, Any]] = []

    if use_llm and client is not None:
        toc_payload: List[Dict[str, Any]] = []
        for i in range(min(toc_pages, scan_pages)):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            if text:
                toc_payload.append({"page_num": i + 1, "text": text[:4000]})
        try:
            toc_parsed = _extract_toc_map_from_pages(client, llm_model, toc_payload)
            if toc_parsed.entries:
                reading_starts = _normalize_toc_entries(
                    toc_parsed.entries, n_pages, debug=debug
                )
                if debug:
                    logger.info(f"TOC-derived reading_starts: {len(reading_starts)}")
        except Exception as e:
            logger.warning(f"TOC extraction failed: {e}")

    if not reading_starts:
        for i in range(scan_pages):
            page = doc.load_page(i)
            text_dict = page.get_text("dict") or {}
            text = page.get_text("text") or ""
            if not text:
                continue
            if not _is_candidate_page(text, text_dict):
                continue

            page_num = i + 1
            if debug:
                head = " ".join(text[:200].split())
                logger.info(f"Candidate page p{page_num}: {head}")
            if use_llm and client is not None:
                if debug:
                    logger.info(f"LLM confirm p{page_num}")
                try:
                    parsed = _confirm_reading_start(client, llm_model, text)
                except Exception as e:
                    logger.warning(f"LLM confirm failed p{page_num}: {e}")
                    continue
                if not parsed.is_reading_start:
                    continue
                reading_starts.append({
                    "reading_id": _normalize_reading_id(parsed.reading_id, len(reading_starts) + 1),
                    "reading_title": (parsed.reading_title or "").strip(),
                    "page_start": page_num,
                })
            else:
                # Regex fallback: assign sequential IDs if LLM is disabled
                reading_starts.append({
                    "reading_id": str(len(reading_starts) + 1),
                    "reading_title": "",
                    "page_start": page_num,
                })

    # De-duplicate by reading_id and page_start
    dedup: Dict[str, Dict[str, Any]] = {}
    for r in reading_starts:
        key = f"{r['reading_id']}|{r['page_start']}"
        if key not in dedup:
            dedup[key] = r
    reading_starts = sorted(dedup.values(), key=lambda x: x["page_start"])
    cleaned: List[Dict[str, Any]] = []
    last_start = 0
    for r in reading_starts:
        start = int(r["page_start"])
        if start <= last_start:
            logger.warning(
                f"Skipping non-increasing start page {start} for {doc_id} ({r['reading_id']})"
            )
            continue
        cleaned.append(r)
        last_start = start
    reading_starts = cleaned

    if not reading_starts:
        raise ValueError(f"No reading starts detected for {doc_id}")

    # Compute page_end
    for idx, r in enumerate(reading_starts):
        if idx < len(reading_starts) - 1:
            r["page_end"] = reading_starts[idx + 1]["page_start"] - 1
        else:
            r["page_end"] = n_pages

    # Merge into output reading_map.json
    if out_path.exists():
        data = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        data = {}
    data[doc_id] = reading_starts
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Updated reading_map: {out_path}")
    return data
