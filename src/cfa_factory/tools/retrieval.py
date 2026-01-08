from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

import chromadb
from chromadb.utils import embedding_functions
from loguru import logger

from dotenv import load_dotenv
from cfa_factory.schemas.models import EvidencePacket, Chunk, TopKQuery, RetrievalHit

load_dotenv()


def _run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S")


def _collection_exists(chroma_dir: Path, name: str) -> bool:
    try:
        client = chromadb.PersistentClient(path=str(chroma_dir))
        cols = client.list_collections()
        return any(c.name == name for c in cols)
    except Exception as e:
        logger.warning(f"Failed to list collections in {chroma_dir}: {e}")
        return False


def _reading_id_aliases(reading_id: str) -> List[str]:
    raw = (reading_id or "").strip()
    ids = {raw}
    m = re.search(r"(\\d+)", raw)
    if m:
        n = int(m.group(1))
        ids.add(str(n))
        ids.add(f"R{n}")
        ids.add(f"Reading {n}")
        ids.add(f"Learning Module {n}")
    return list(ids)


def load_chunks_for_reading(chunks_file: Path, reading_id: str) -> List[Chunk]:
    aliases = {rid.lower() for rid in _reading_id_aliases(reading_id)}
    items: List[Chunk] = []
    with chunks_file.open("r", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            rid = (r.get("reading_id") or "").strip().lower()
            if rid and rid in aliases:
                items.append(Chunk.model_validate(r))
    return items


# Avoid duplicate definition by importing or redefining Dummy. 
# Since Dummy is stateless, we can just use a lambda or generic if passed to get_collection.
# However, for query_embeddings, we MUST compute the actual vector.

from google import genai
from cfa_factory.tools.index_store import DummyEmbeddingFunction  # Reuse if possible or redefine


def compute_query_embedding(query: str) -> List[float]:
    client = genai.Client()
    try:
        resp = client.models.embed_content(
            model="text-embedding-004",
            contents=query,
            config={"output_dimensionality": 768}
        )
        # For single string, SDK may return one embedding
        # access logic similar to index_store
        val = list(resp.embeddings[0].values)
        if len(val) != 768:
            raise ValueError(f"Query embedding dim {len(val)} != 768")
        return val
    except Exception as e:
        logger.error(f"Query embedding failed: {e}")
        # Fail fast for query
        raise e


def chroma_query(
    chroma_dir: str | Path,
    query: str,
    k: int = 12,
    where: Optional[Dict[str, Any]] = None
) -> List[RetrievalHit]:
    chroma_dir = Path(chroma_dir)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    
    # Use Dummy EF to match the collection definition
    # Note: We do NOT rely on it for embedding the query text here.
    # We pass `query_embeddings` explicitly to avoid using the internal EF.
    col = client.get_collection(name="cfa_chunks", embedding_function=DummyEmbeddingFunction())

    # 1. Compute query vector explicitly using Gemini (768 dim)
    q_vec = compute_query_embedding(query)

    # 2. Pass query_embeddings instead of query_texts
    res = col.query(
        query_embeddings=[q_vec],
        n_results=k,
        where=where
        # query_texts is optional if embeddings are provided
    )
    
    ids = res["ids"][0]
    dists = res["distances"][0]
    metas = res["metadatas"][0]
    docs = res["documents"][0]

    hits: List[RetrievalHit] = []
    for cid, dist, meta, doc in zip(ids, dists, metas, docs):
        hits.append(RetrievalHit(
            chunk_id=cid,
            doc_id=meta.get("doc_id"),
            page=int(meta.get("page")),
            score=float(1.0 - dist),  # 粗略转成相似度（仅供排序展示）
            snippet=(doc[:240] + "…") if len(doc) > 240 else doc
        ))
    return hits


def build_evidence_packet(
    doc_id: str,
    reading_id: str,
    chunks_file: str | Path,
    chroma_dir: str | Path,
    out_runs_dir: str | Path,
    cross_ref: bool = False,  # Enable cross-book RAG
    unified_chroma_dir: str | Path | None = None,  # Path to unified index
) -> EvidencePacket:
    """Build evidence packet for a reading.
    
    Args:
        cross_ref: If True, add queries to unified index for cross-book references
        unified_chroma_dir: Path to unified ChromaDB (used when cross_ref=True)
    """
    chunks_file = Path(chunks_file)
    chroma_dir = Path(chroma_dir)
    out_runs_dir = Path(out_runs_dir)
    if unified_chroma_dir:
        unified_chroma_dir = Path(unified_chroma_dir)
    
    run_id = _run_id()
    out_dir = out_runs_dir / doc_id / reading_id / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if not chunks_file.exists():
        raise FileNotFoundError(f"Missing chunks file: {chunks_file}")

    reading_fulltext = load_chunks_for_reading(chunks_file, reading_id)
    if not reading_fulltext:
        raise ValueError(f"No chunks found for reading_id={reading_id} in {chunks_file}. "
                         f"Check assets/reading_map.json page ranges.")

    # Same-book queries
    default_queries = [
        f"{doc_id} {reading_id} key concepts",
        f"{reading_id} exam traps common mistakes",
        f"{reading_id} formula intuition example"
    ]

    top_k: List[TopKQuery] = []
    for q in default_queries:
        try:
            hits = chroma_query(chroma_dir, q, k=12)
        except Exception as e:
            msg = str(e)
            if (
                ("does not exist" in msg or "NotFound" in msg)
                and unified_chroma_dir
                and unified_chroma_dir.exists()
            ):
                logger.warning(
                    "Doc index missing for %s; fallback to unified index with doc filter",
                    doc_id,
                )
                hits = chroma_query(unified_chroma_dir, q, k=12, where={"doc_id": doc_id})
            else:
                raise
        top_k.append(TopKQuery(query=q, k=12, hits=hits))

    # Cross-book queries (uses unified index)
    cross_ref_queries: List[TopKQuery] = []
    if cross_ref and unified_chroma_dir and unified_chroma_dir.exists():
        if not _collection_exists(unified_chroma_dir, "cfa_chunks"):
            logger.warning("Unified index missing cfa_chunks; skipping cross-ref queries.")
        else:
            logger.info("Adding cross-book RAG queries from unified index...")
            cross_queries = [
                f"{reading_id} related concepts from other CFA volumes",
                f"{reading_id} Schweser explanation simplified version",
                f"{reading_id} prerequisite knowledge foundation from V1 Quant",
                f"{reading_id} practical application real world examples",
            ]
            for q in cross_queries:
                try:
                    hits = chroma_query(unified_chroma_dir, q, k=8)
                except Exception as e:
                    logger.warning(f"Unified index query failed; skipping cross-ref: {e}")
                    hits = []
                cross_ref_queries.append(TopKQuery(query=q, k=8, hits=hits))
            logger.info(f"Added {len(cross_ref_queries)} cross-book query results")

    packet = EvidencePacket(
        doc_id=doc_id,
        reading_id=reading_id,
        run_id=run_id,
        reading_fulltext=reading_fulltext,
        top_k=top_k + cross_ref_queries,  # Combine both query sets
        conflicts=[],
        meta={
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "queries": default_queries + ([q.query for q in cross_ref_queries] if cross_ref_queries else []),
            "cross_ref_enabled": cross_ref,
            "notes": "Evidence packet with optional cross-book RAG from unified index."
        }
    )

    # Schema gate
    _ = EvidencePacket.model_validate(packet.model_dump())

    out_file = out_dir / "evidence_packet.json"
    out_file.write_text(json.dumps(packet.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Wrote evidence packet: {out_file}")
    return packet
