from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field

# --- Router ---
class LessonPlanSchema(BaseModel):
    mode: Literal["MODE_PHYSICS", "MODE_GAME", "MODE_SYSTEM", "MODE_ETHICS"]
    segment_minutes: Optional[int] = Field(
        default=None,
        description="Legacy field. No fixed duration; null means unconstrained."
    )
    required_beats: List[str]
    retrieval_queries: List[str]
    lesson_outline: str = Field(
        description="A single sentence narrative outline of the lesson flow.",
        max_length=300
    )
    # Auto-routing decision
    recommended_depth: Literal["SINGLE_ROUND", "MULTI_ROUND"] = Field(
        description="Recommended debate depth based on content complexity."
    )
    depth_rationale: str = Field(
        description="One sentence explaining why this depth was chosen.",
        max_length=500
    )

# --- Professor ---
class Claim(BaseModel):
    claim_id: str
    statement_en: str
    citations: List[str]  # doc_id|page|chunk_id
    knowledge_scope: Literal["IN_PDF", "OUTSIDE_PDF"]
    applied_lens: Optional[str] = Field(description="The philosophical lens used (e.g. 'First Principles')")
    section_id: Optional[str] = Field(
        default=None,
        description="Outline section_id this claim belongs to (e.g., 'S1')."
    )
    # Toulmin model fields (required)
    data: str = Field(description="Evidence or data supporting the claim.")
    warrant: str = Field(description="Reasoning that links data to the claim.")
    backing: str = Field(description="Support for the warrant (theory or principle).")
    rebuttal: str = Field(description="Counterexample, exception, or boundary condition.")
    # NEW: Derivation path for "把书读厚"
    derivation_path: Optional[str] = Field(
        default=None,
        description="Step-by-step derivation or reasoning chain (for MODE_PHYSICS). Show HOW you arrived at the claim, not just WHAT it says."
    )
    cross_references: Optional[List[str]] = Field(
        default=None,
        description="Related readings or concepts from other CFA topics (e.g. 'R2:Duration', 'Ethics:Fiduciary')."
    )
    counterintuitive: Optional[str] = Field(
        default=None,
        description="What common intuition does this contradict? Why is it surprising?"
    )

class ProfessorClaimsSchema(BaseModel):
    claims: List[Claim]


class OutlineSection(BaseModel):
    section_id: str
    title: str
    key_points: List[str]
    key_questions: List[str]
    citations: List[str]
    weight: Optional[int] = Field(
        default=1,
        description="Relative weight for section emphasis (1=low, 2=med, 3=high)."
    )


class LectureOutlineSchema(BaseModel):
    reading_id: str
    title: str
    target_minutes: int
    sections: List[OutlineSection]

# --- Student ---
class Challenge(BaseModel):
    target_claim_id: str
    attack_type: Literal["EDGE_CASE", "MODEL_RISK", "INCENTIVE", "BEHAVIORAL"]
    challenge_statement: str
    citations: List[str]
    applied_lens: Optional[str] = Field(description="The philosophical lens used (e.g. 'Inversion')")
    section_id: Optional[str] = Field(
        default=None,
        description="Outline section_id this challenge belongs to (e.g., 'S1')."
    )

class StudentAttacksSchema(BaseModel):
    challenges: List[Challenge]

# --- Synthesis ---
class SynthesisClaimsSchema(BaseModel):
    claims: List[Claim]
    reasoning: str

# --- Verifier ---
class Verdict(BaseModel):
    claim_id: str
    status: Literal["PASS", "WEAK", "HALLUCINATION", "OUT_OF_SCOPE"]  # Added OUT_OF_SCOPE
    reason: str
    fix_suggestion: Optional[str] = None
    # NEW: Scope checking
    scope_check: Optional[Literal["IN_SCOPE", "TANGENTIAL", "OFF_TOPIC"]] = Field(
        default=None,
        description="Whether this claim stays within the reading's scope."
    )
    citation_verified: Optional[bool] = Field(
        default=None,
        description="Whether all citations actually exist in the evidence packet."
    )

class VerifierOutputSchema(BaseModel):
    verdicts: List[Verdict]
    overall_decision: Literal["PROCEED", "RETRIEVE_MORE", "REWRITE", "REFOCUS"]  # Added REFOCUS
    scope_summary: Optional[str] = Field(
        default=None,
        description="Summary of scope issues if any claims drifted off-topic."
    )

# --- Continuity Gate ---
class ContinuityIssue(BaseModel):
    type: Literal["TONE", "FACTUAL", "FORMATTING", "SCOPE"]  # Added SCOPE
    description: str

class ContinuityReportSchema(BaseModel):
    passed: bool
    issues: List[ContinuityIssue]

# --- Search Agent ---
class SearchSource(BaseModel):
    title: str
    url: str
    summary: str


class SearchContextSchema(BaseModel):
    query: str
    sources: List[SearchSource]

# --- Editor ---
class QuizOption(BaseModel):
    id: str
    text: str

class Quiz(BaseModel):
    type: Literal["MCQ", "TF"]
    question_zh: str
    choices: Optional[List[str]] = None
    answer: str | bool
    explanation_zh: str
    answer_citations: List[str]

    @classmethod
    def _is_bool(cls, value: Any) -> bool:
        return isinstance(value, bool)

    @classmethod
    def _is_str(cls, value: Any) -> bool:
        return isinstance(value, str)

    def model_post_init(self, __context: Any) -> None:
        if self.type == "MCQ":
            if not self.choices or len(self.choices) < 2:
                raise ValueError("MCQ requires choices with at least 2 options")
            if not self._is_str(self.answer):
                raise ValueError("MCQ answer must be a string choice label")
        if self.type == "TF":
            if not self._is_bool(self.answer):
                raise ValueError("TF answer must be boolean")

class Scene(BaseModel):
    beat: str
    speaker: Literal["Professor", "Student", "Narrator"]
    display_zh: str
    spoken_zh: str
    citations: List[str]
    visual_refs: List[str]
    quiz: Optional[Quiz] = None

class VideoScriptSchema(BaseModel):
    segment_id: str
    duration_est_min: Optional[int] = None
    scenes: List[Scene]


# --- English Script (for translation) ---
class SceneEn(BaseModel):
    beat: str
    speaker: Literal["Professor", "Student", "Narrator"]
    display_en: str
    spoken_en: str
    citations: List[str]
    visual_refs: List[str]
    quiz: Optional[Quiz] = None


class VideoScriptEnSchema(BaseModel):
    segment_id: str
    duration_est_min: Optional[int] = None
    scenes: List[SceneEn]


# =============================================================================
# TWO-PHASE GENERATION SCHEMAS
# =============================================================================

# --- Phase A: Outline Generation ---
class SceneOutline(BaseModel):
    """Single scene outline (Phase A output)"""
    scene_id: str  # e.g. "S01", "S02"
    beat: str  # misconception, first_principles, numeric_example, exam_trap, synthesis, quiz
    title_zh: str  # 中文标题 (简洁)
    key_points: List[str] = Field(
        description="3-5 核心要点，该场景必须传达的内容",
        min_length=2,
        max_length=6
    )
    speaker: Literal["Professor", "Student", "Narrator"]
    citations: List[str] = Field(default_factory=list)
    target_words: int = Field(default=250, description="目标字数 200-300")


class ScriptOutlineSchema(BaseModel):
    """Phase A output: Full script outline with variable scene count"""
    segment_id: str
    duration_est_min: Optional[int] = None
    total_scenes: int = Field(description="场景总数，按阅读内容规模确定")
    scenes: List[SceneOutline]


# --- Phase B: Scene Expansion (English output) ---
class ExpandedScene(BaseModel):
    """Phase B output: Single expanded scene with ENGLISH dialogue (translated to Chinese in Phase C)"""
    scene_id: str
    beat: str
    display_en: str = Field(description="Screen display text: formulas, diagram descriptions, key terms")
    spoken_en: str = Field(
        description="Detailed English dialogue 200-300 words",
        min_length=50
    )
    speaker: Literal["Professor", "Student", "Narrator"]
    citations: List[str]
    visual_refs: List[str] = Field(default_factory=list)
    quiz: Optional[Quiz] = None
