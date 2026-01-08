from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional


@dataclass(frozen=True)
class Document:
    doc_id: str
    kind: str  # official / schweser
    path: Path
    title: str


def load_manifest(path: Path) -> List[Document]:
    data = json.loads(path.read_text(encoding="utf-8"))
    docs = []
    for d in data["documents"]:
        docs.append(
            Document(
                doc_id=d["doc_id"],
                kind=d["kind"],
                path=Path(d["path"]),
                title=d.get("title", "")
            )
        )
    return docs


def load_reading_map(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
