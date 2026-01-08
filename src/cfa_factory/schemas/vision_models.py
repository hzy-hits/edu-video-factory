from __future__ import annotations

from pydantic import BaseModel, Field
from typing import List, Literal, Optional


class FormulaAsset(BaseModel):
    type: Literal["formula"] = "formula"
    display_latex: str = Field(..., description="LaTeX for subtitles/handout.")
    spoken_en: str = Field(..., description="Natural spoken English for TTS, do NOT read symbols.")
    meaning_en: str = Field(..., description="Short explanation of what the formula means in CFA context.")
    page: int


class FigureAsset(BaseModel):
    type: Literal["figure"] = "figure"
    caption_en: str
    blind_description_en: str = Field(..., description=">=200 characters describing trends/axes/intersections.")
    key_points: List[str]
    page: int


class TableAsset(BaseModel):
    type: Literal["table"] = "table"
    title_en: Optional[str] = None
    headers: List[str]
    rows: List[List[str]]
    notes_en: Optional[str] = None
    page: int


class VisionExtractResult(BaseModel):
    doc_id: str
    reading_id: str
    page: int
    assets: List[FormulaAsset | FigureAsset | TableAsset]
