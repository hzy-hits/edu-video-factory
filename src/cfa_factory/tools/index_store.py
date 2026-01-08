from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
import chromadb
from chromadb.utils import embedding_functions
from loguru import logger
from google import genai

from cfa_factory.schemas.models import Chunk


load_dotenv()


def load_chunks_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def compute_embeddings_in_batches(client, model: str, texts: List[str]) -> List[List[float]]:
    embeddings = []
    BATCH_SIZE = 50
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        try:
            resp = client.models.embed_content(
                model=model,
                contents=batch,
                config={"output_dimensionality": 768}
            )
            for e in resp.embeddings:
                vals = list(e.values)
                if len(vals) != 768:
                    raise ValueError(f"Wrong dim: {len(vals)}")
                embeddings.append(vals)
        except Exception as e:
            logger.error(f"Embedding failed batch {i}: {e}")
            raise e
    return embeddings


class DummyEmbeddingFunction(embedding_functions.EmbeddingFunction):
    def __call__(self, input: List[str]) -> List[List[float]]:
        # Should not be called during insert if embeddings provided
        return [[0.0] * 768 for _ in input]


def build_chroma_index(chunks_dir: str | Path, chroma_dir: str | Path) -> None:
    chroma_dir = Path(chroma_dir)
    chunks_dir = Path(chunks_dir)
    chroma_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Building Chroma index at {chroma_dir}")

    client = chromadb.PersistentClient(path=str(chroma_dir))
    
    # Force clean slate
    try:
        client.delete_collection("cfa_chunks")
    except Exception:
        pass

    # Use Dummy EF to enforce 768 dimension
    col = client.create_collection(
        name="cfa_chunks",
        embedding_function=DummyEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"}
    )

    to_add_ids, to_add_docs, to_add_meta = [], [], []
    already_seen_this_run = set()

    # Support both single file and directory of files
    if chunks_dir.is_file():
        jsonl_files = [chunks_dir]
    else:
        jsonl_files = sorted(chunks_dir.glob("*.jsonl"))
    
    for p in jsonl_files:
        rows = load_chunks_jsonl(p)
        for r in rows:
            cid = r["chunk_id"]
            if cid in already_seen_this_run:
                continue
            already_seen_this_run.add(cid)
            to_add_ids.append(cid)
            to_add_docs.append(r["content"])
            
            meta = {
                "doc_id": r["doc_id"],
                "kind": r["kind"],
                "page": r["page"],
                "reading_id": r.get("reading_id") or "",
                "no_cut": str(r.get("no_cut", False))
            }
            to_add_meta.append(meta)

    if to_add_ids:
        logger.info(f"Computing embeddings for {len(to_add_ids)} chunks...")
        genai_client = genai.Client()
        embeddings = compute_embeddings_in_batches(genai_client, "text-embedding-004", to_add_docs)
        
        dims = set(len(x) for x in embeddings)
        logger.info(f"Embedding dimensions present: {dims}")

        # ChromaDB has max batch size of 5461, so add in batches
        CHROMA_BATCH_SIZE = 5000
        total = len(to_add_ids)
        for i in range(0, total, CHROMA_BATCH_SIZE):
            end = min(i + CHROMA_BATCH_SIZE, total)
            col.add(
                ids=to_add_ids[i:end],
                documents=to_add_docs[i:end],
                metadatas=to_add_meta[i:end],
                embeddings=embeddings[i:end]
            )
            logger.info(f"Added batch {i//CHROMA_BATCH_SIZE + 1}: {end - i} chunks (total: {end}/{total})")
        
        logger.info(f"Added {total} chunks to Chroma")
    else:
        logger.info("No chunks to add")
