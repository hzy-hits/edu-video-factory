# =============================================================================
# CFA Factory Core Agents (Google ADK Native)
# =============================================================================
# All agents use native Google ADK LlmAgent with {state.key} template syntax.
# DeepSeek Editor uses our custom DeepSeekAgent wrapper.
# =============================================================================

import os

from cfa_factory.agents.framework import LlmAgent, SequentialAgent, LoopAgent, DeepSeekAgent, PerSceneTranslatorAgent, google_search
from cfa_factory.agents.schemas import (
    LessonPlanSchema,
    ProfessorClaimsSchema,
    StudentAttacksSchema,
    SynthesisClaimsSchema,
    VerifierOutputSchema,
    VideoScriptSchema,
    VideoScriptEnSchema,
    ContinuityReportSchema,
    SearchContextSchema,
    LectureOutlineSchema,
    # Two-phase schemas
    ScriptOutlineSchema,
    ExpandedScene,
)
from cfa_factory.agents.prompts import (
    ROUTER_TEMPLATE,
    TA_OUTLINE_TEMPLATE,
    PROFESSOR_TEMPLATE,
    STUDENT_TEMPLATE,
    SYNTHESIS_TEMPLATE,
    VERIFIER_TEMPLATE,
    EDITOR_TEMPLATE,
    EDITOR_FIX_TEMPLATE,
    LECTURE_DRAFTER_TEMPLATE,
    DIALOGUE_EXPANDER_TEMPLATE,
    DIALOGUE_STRICT_EXPANDER_TEMPLATE,
    CONTINUITY_TEMPLATE,
    PROFESSOR_DEEPDIVE_TEMPLATE,
    STUDENT_DEEPDIVE_TEMPLATE,
    SEARCH_AGENT_TEMPLATE,
    DEEPSEEK_TRANSLATOR_TEMPLATE,
    # Two-phase templates
    OUTLINE_GENERATOR_TEMPLATE,
    SCENE_EXPANDER_TEMPLATE,
)

# =============================================================================
# Agent Definitions
# =============================================================================

# --- 1. Router ---
router = LlmAgent(
    name="router",
    model="gemini-3-flash-preview",
    instruction=ROUTER_TEMPLATE,
    output_schema=LessonPlanSchema,
    output_key="lesson_plan",
    description="Classifies CFA reading into pedagogical mode"
)

# --- 1.5. Teaching Assistant Outline ---
ta_outline = LlmAgent(
    name="ta_outline",
    model="gemini-3-flash-preview",
    instruction=TA_OUTLINE_TEMPLATE,
    output_schema=LectureOutlineSchema,
    output_key="lecture_outline",
    description="Builds lecture outline and key questions"
)

# --- 2. Debate Agents (Round 1: Core Claims) ---
professor = LlmAgent(
    name="professor",
    model="gemini-3-pro-preview",
    instruction=PROFESSOR_TEMPLATE,
    output_schema=ProfessorClaimsSchema,
    output_key="professor_claims",
    description="Generates first-principles claims from evidence"
)

student = LlmAgent(
    name="student",
    model="gemini-3-flash-preview",
    instruction=STUDENT_TEMPLATE,
    output_schema=StudentAttacksSchema,
    output_key="student_challenges",
    description="Stress-tests professor claims"
)

synthesis = LlmAgent(
    name="synthesis",
    model="gemini-3-pro-preview",
    instruction=SYNTHESIS_TEMPLATE,
    output_schema=SynthesisClaimsSchema,
    output_key="synthesis_claims",
    description="Reconciles claims and challenges"
)

# --- 3. Deep-Dive Agents (Round 2-3: Exam Traps, Edge Cases) ---
professor_deepdive = LlmAgent(
    name="professor_deepdive",
    model="gemini-3-pro-preview",
    instruction=PROFESSOR_DEEPDIVE_TEMPLATE,
    output_schema=ProfessorClaimsSchema,
    output_key="professor_claims_deepdive",
    description="Generates deep-dive claims on exam traps and edge cases"
)

student_deepdive = LlmAgent(
    name="student_deepdive",
    model="gemini-3-flash-preview",
    instruction=STUDENT_DEEPDIVE_TEMPLATE,
    output_schema=StudentAttacksSchema,
    output_key="student_challenges_deepdive",
    description="Attacks deep-dive claims with real-world failure modes"
)

# --- 4. Gatekeepers ---
verifier = LlmAgent(
    name="verifier",
    model="gemini-3-flash-preview",
    instruction=VERIFIER_TEMPLATE,
    output_schema=VerifierOutputSchema,
    output_key="verifier_report",
    description="Audits claims against evidence"
)

# --- Search Agent (only agent allowed to call google_search) ---
search_agent = LlmAgent(
    name="search_agent",
    model="gemini-3-flash-preview",
    instruction=SEARCH_AGENT_TEMPLATE,
    tools=[google_search],
    output_schema=None,
    output_key="search_context",
    description="Performs web search and summarizes sources with URLs"
)

# --- 5. Editors ---
editor_deepseek = DeepSeekAgent(
    name="editor_deepseek",
    deepseek_model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY",
    instruction=EDITOR_TEMPLATE,
    output_schema=None,
    output_key="editor_raw",
    description="Generates video script using DeepSeek"
)

editor_fallback = LlmAgent(
    name="editor_fallback",
    model="gemini-3-flash-preview",
    instruction=EDITOR_TEMPLATE,
    output_schema=None,
    output_key="editor_raw",
    description="Fallback editor using Gemini"
)

use_deepseek_editor = os.getenv("EDITOR_BACKEND", "deepseek").lower() == "deepseek"
if use_deepseek_editor and not os.getenv("DEEPSEEK_API_KEY"):
    use_deepseek_editor = False

editor_agent = editor_deepseek if use_deepseek_editor else editor_fallback

editor_fix = LlmAgent(
    name="editor_fix",
    model="gemini-3-flash-preview",
    instruction=EDITOR_FIX_TEMPLATE,
    output_schema=VideoScriptSchema,
    output_key="editor_script",
    description="Repairs raw editor output into strict VideoScript schema"
)

lecture_drafter = LlmAgent(
    name="lecture_drafter",
    model="gemini-3-pro-preview",
    instruction=LECTURE_DRAFTER_TEMPLATE,
    output_schema=VideoScriptEnSchema,
    output_key="professor_lecture",
    description="Professor produces a dialogue-style lecture draft"
)

dialogue_expander = LlmAgent(
    name="dialogue_expander",
    model="gemini-3-pro-preview",
    instruction=DIALOGUE_EXPANDER_TEMPLATE,
    output_schema=VideoScriptEnSchema,
    output_key="english_script",
    description="Expands draft script into multi-turn dialogue"
)

dialogue_expander_strict = LlmAgent(
    name="dialogue_expander_strict",
    model="gemini-3-pro-preview",
    instruction=DIALOGUE_STRICT_EXPANDER_TEMPLATE,
    output_schema=VideoScriptEnSchema,
    output_key="english_script",
    description="Enforces minimum words per scene"
)

use_deepseek_translator = bool(os.getenv("DEEPSEEK_API_KEY"))
translator_fallback = LlmAgent(
    name="translator_fallback",
    model="gemini-3-flash-preview",
    instruction=DEEPSEEK_TRANSLATOR_TEMPLATE,
    output_schema=VideoScriptSchema,
    output_key="editor_script",
    description="Fallback translator to Chinese (strict JSON output)"
)
production_translator = PerSceneTranslatorAgent(
    name="production_translator",
    output_key="editor_script",
    raw_output_key="translated_raw",
    max_retries=5,
    description="Per-scene translation to Chinese using DeepSeek (no JSON truncation)"
) if use_deepseek_translator else translator_fallback

continuity_gate = LlmAgent(
    name="continuity_gate",
    model="gemini-3-flash-preview",
    instruction=CONTINUITY_TEMPLATE,
    output_schema=ContinuityReportSchema,
    output_key="continuity_report",
    description="Validates script consistency"
)

# =============================================================================
# Pre-built Workflows
# =============================================================================
# NOTE: ADK doesn't allow the same agent instance in multiple workflows.
# We create TWO separate pipelines - single-round and multi-round.

# --- Main Debate Pipeline (Single Round) ---
# Uses: router, professor, student, synthesis, verifier
debate_pipeline = SequentialAgent(
    name="debate_pipeline",
    sub_agents=[router, ta_outline, search_agent, professor, student, synthesis, verifier],
    description="Executes the full debate workflow from routing to verification"
)

# --- Multi-Round Debate Pipeline (For Full Volume Coverage) ---
# Uses: professor_deepdive, student_deepdive (separate instances)
# NOTE: This pipeline reuses synthesis for Round 2 by design - it accumulates context.
multi_round_debate_pipeline = SequentialAgent(
    name="multi_round_debate_pipeline",
    sub_agents=[
        # We need FRESH agent instances for multi-round
        # Router
        LlmAgent(
            name="mr_router",
            model="gemini-3-flash-preview",
            instruction=ROUTER_TEMPLATE,
            output_schema=LessonPlanSchema,
            output_key="lesson_plan",
            description="Classifies CFA reading into pedagogical mode"
        ),
        LlmAgent(
            name="mr_ta_outline",
            model="gemini-3-flash-preview",
            instruction=TA_OUTLINE_TEMPLATE,
            output_schema=LectureOutlineSchema,
            output_key="lecture_outline",
            description="Builds lecture outline and key questions"
        ),
        # Search (web)
        LlmAgent(
            name="mr_search_agent",
            model="gemini-3-flash-preview",
            instruction=SEARCH_AGENT_TEMPLATE,
            tools=[google_search],
            output_schema=None,
            output_key="search_context",
            description="Performs web search and summarizes sources with URLs"
        ),
        # Round 1: Core Claims
        LlmAgent(
            name="mr_professor",
            model="gemini-3-pro-preview",
            instruction=PROFESSOR_TEMPLATE,
            output_schema=ProfessorClaimsSchema,
            output_key="professor_claims",
            description="Generates first-principles claims from evidence"
        ),
        LlmAgent(
            name="mr_student",
            model="gemini-3-flash-preview",
            instruction=STUDENT_TEMPLATE,
            output_schema=StudentAttacksSchema,
            output_key="student_challenges",
            description="Stress-tests professor claims"
        ),
        LlmAgent(
            name="mr_synthesis_r1",
            model="gemini-3-pro-preview",
            instruction=SYNTHESIS_TEMPLATE,
            output_schema=SynthesisClaimsSchema,
            output_key="synthesis_claims",
            description="Reconciles claims and challenges"
        ),
        # Round 2: Deep-Dive
        professor_deepdive,
        student_deepdive,
        LlmAgent(
            name="mr_synthesis_r2",
            model="gemini-3-pro-preview",
            instruction=SYNTHESIS_TEMPLATE,
            output_schema=SynthesisClaimsSchema,
            output_key="synthesis_claims_r2",
            description="Final synthesis of all claims"
        ),
        # Verification
        LlmAgent(
            name="mr_verifier",
            model="gemini-3-flash-preview",
            instruction=VERIFIER_TEMPLATE,
            output_schema=VerifierOutputSchema,
            output_key="verifier_report",
            description="Audits claims against evidence"
        ),
    ],
    description="Multi-round debate for comprehensive content coverage (2-4 hour video output)"
)

# --- Production Pipeline (with Editor) ---
# Single-round + Lecture Draft + Dialogue Expansion + Translation + Continuity
production_pipeline = SequentialAgent(
    name="production_pipeline",
    sub_agents=[
        # Fresh instances for production
        LlmAgent(
            name="prod_router",
            model="gemini-3-flash-preview",
            instruction=ROUTER_TEMPLATE,
            output_schema=LessonPlanSchema,
            output_key="lesson_plan",
            description="Classifies CFA reading into pedagogical mode"
        ),
        LlmAgent(
            name="prod_ta_outline",
            model="gemini-3-flash-preview",
            instruction=TA_OUTLINE_TEMPLATE,
            output_schema=LectureOutlineSchema,
            output_key="lecture_outline",
            description="Builds lecture outline and key questions"
        ),
        LlmAgent(
            name="prod_search_agent",
            model="gemini-3-flash-preview",
            instruction=SEARCH_AGENT_TEMPLATE,
            tools=[google_search],
            output_schema=None,
            output_key="search_context",
            description="Performs web search and summarizes sources with URLs"
        ),
        LlmAgent(
            name="prod_professor",
            model="gemini-3-pro-preview",
            instruction=PROFESSOR_TEMPLATE,
            output_schema=ProfessorClaimsSchema,
            output_key="professor_claims",
            description="Generates first-principles claims from evidence"
        ),
        LlmAgent(
            name="prod_student",
            model="gemini-3-flash-preview",
            instruction=STUDENT_TEMPLATE,
            output_schema=StudentAttacksSchema,
            output_key="student_challenges",
            description="Stress-tests professor claims"
        ),
        LlmAgent(
            name="prod_synthesis",
            model="gemini-3-pro-preview",
            instruction=SYNTHESIS_TEMPLATE,
            output_schema=SynthesisClaimsSchema,
            output_key="synthesis_claims",
            description="Reconciles claims and challenges"
        ),
        LlmAgent(
            name="prod_verifier",
            model="gemini-3-flash-preview",
            instruction=VERIFIER_TEMPLATE,
            output_schema=VerifierOutputSchema,
            output_key="verifier_report",
            description="Audits claims against evidence"
        ),
        # Editor - generates video script (DeepSeek or Gemini fallback)
        lecture_drafter,
        # Dialogue Expander - lengthen dialogue without new facts
        dialogue_expander,
        # Dialogue Expander (strict length pass)
        dialogue_expander_strict,
        # Translator - English to Chinese (DeepSeek preferred, retries on invalid JSON)
        production_translator,
        # Continuity Gate - validates script
        LlmAgent(
            name="prod_continuity",
            model="gemini-3-flash-preview",
            instruction=CONTINUITY_TEMPLATE,
            output_schema=ContinuityReportSchema,
            output_key="continuity_report",
            description="Validates script consistency"
        ),
    ],
    description="Full production pipeline including video script generation"
)

# --- Production Pipeline (English only, no translation) ---
# Single-round + Lecture Draft + Dialogue Expansion (English output)
production_pipeline_en = SequentialAgent(
    name="production_pipeline_en",
    sub_agents=[
        LlmAgent(
            name="prod_en_router",
            model="gemini-3-flash-preview",
            instruction=ROUTER_TEMPLATE,
            output_schema=LessonPlanSchema,
            output_key="lesson_plan",
            description="Classifies CFA reading into pedagogical mode"
        ),
        LlmAgent(
            name="prod_en_ta_outline",
            model="gemini-3-flash-preview",
            instruction=TA_OUTLINE_TEMPLATE,
            output_schema=LectureOutlineSchema,
            output_key="lecture_outline",
            description="Builds lecture outline and key questions"
        ),
        LlmAgent(
            name="prod_en_search_agent",
            model="gemini-3-flash-preview",
            instruction=SEARCH_AGENT_TEMPLATE,
            tools=[google_search],
            output_schema=None,
            output_key="search_context",
            description="Performs web search and summarizes sources with URLs"
        ),
        LlmAgent(
            name="prod_en_professor",
            model="gemini-3-pro-preview",
            instruction=PROFESSOR_TEMPLATE,
            output_schema=ProfessorClaimsSchema,
            output_key="professor_claims",
            description="Generates first-principles claims from evidence"
        ),
        LlmAgent(
            name="prod_en_student",
            model="gemini-3-flash-preview",
            instruction=STUDENT_TEMPLATE,
            output_schema=StudentAttacksSchema,
            output_key="student_challenges",
            description="Stress-tests professor claims"
        ),
        LlmAgent(
            name="prod_en_synthesis",
            model="gemini-3-pro-preview",
            instruction=SYNTHESIS_TEMPLATE,
            output_schema=SynthesisClaimsSchema,
            output_key="synthesis_claims",
            description="Reconciles claims and challenges"
        ),
        LlmAgent(
            name="prod_en_verifier",
            model="gemini-3-flash-preview",
            instruction=VERIFIER_TEMPLATE,
            output_schema=VerifierOutputSchema,
            output_key="verifier_report",
            description="Audits claims against evidence"
        ),
        # Lecture Draft (English) - fresh instance
        LlmAgent(
            name="prod_en_lecture_drafter",
            model="gemini-3-pro-preview",
            instruction=LECTURE_DRAFTER_TEMPLATE,
            output_schema=VideoScriptEnSchema,
            output_key="professor_lecture",
            description="Professor produces a dialogue-style lecture draft"
        ),
        # Dialogue Expander (English) - fresh instance
        LlmAgent(
            name="prod_en_dialogue_expander",
            model="gemini-3-pro-preview",
            instruction=DIALOGUE_EXPANDER_TEMPLATE,
            output_schema=VideoScriptEnSchema,
            output_key="english_script",
            description="Expands draft script into multi-turn dialogue"
        ),
        LlmAgent(
            name="prod_en_dialogue_expander_strict",
            model="gemini-3-pro-preview",
            instruction=DIALOGUE_STRICT_EXPANDER_TEMPLATE,
            output_schema=VideoScriptEnSchema,
            output_key="english_script",
            description="Enforces minimum words per scene"
        ),
    ],
    description="Production pipeline without translation (English output only)"
)

# =============================================================================
# TWO-PHASE GENERATION AGENTS
# =============================================================================

# --- Phase A: Outline Generator ---
outline_generator = LlmAgent(
    name="outline_generator",
    model="gemini-3-pro-preview",  # Pro for structure/logic
    instruction=OUTLINE_GENERATOR_TEMPLATE,
    output_schema=ScriptOutlineSchema,
    output_key="script_outline",
    description="Generates 25-30 scene outline for video script"
)

# --- Phase B: Scene Expander (English output) ---
# This agent is called in a loop, one scene at a time
scene_expander = LlmAgent(
    name="scene_expander",
    model="gemini-3-flash-preview",  # Flash for speed (called 25-30 times)
    instruction=SCENE_EXPANDER_TEMPLATE,
    output_schema=ExpandedScene,
    output_key="expanded_scene",
    description="Expands single scene outline into 200-300 word ENGLISH dialogue"
)

# --- Phase C: DeepSeek Translator (English → Chinese) ---
from cfa_factory.agents.prompts import DEEPSEEK_TRANSLATOR_TEMPLATE

script_translator = DeepSeekAgent(
    name="script_translator",
    deepseek_model="deepseek-chat",
    base_url="https://api.deepseek.com",
    instruction=DEEPSEEK_TRANSLATOR_TEMPLATE,
    output_schema=VideoScriptSchema,
    output_key="translated_script",
    description="Translates English script to natural Chinese using DeepSeek"
)

# --- Two-Phase Pipeline (Phase A only - Phase B is handled by CLI runner) ---
two_phase_pipeline = SequentialAgent(
    name="two_phase_pipeline",
    sub_agents=[
        # Debate agents for content generation
        LlmAgent(
            name="tp_router",
            model="gemini-3-flash-preview",
            instruction=ROUTER_TEMPLATE,
            output_schema=LessonPlanSchema,
            output_key="lesson_plan",
            description="Classifies CFA reading into pedagogical mode"
        ),
        LlmAgent(
            name="tp_search_agent",
            model="gemini-3-flash-preview",
            instruction=SEARCH_AGENT_TEMPLATE,
            tools=[google_search],
            output_schema=None,
            output_key="search_context",
            description="Performs web search and summarizes sources with URLs"
        ),
        LlmAgent(
            name="tp_professor",
            model="gemini-3-pro-preview",
            instruction=PROFESSOR_TEMPLATE,
            output_schema=ProfessorClaimsSchema,
            output_key="professor_claims",
            description="Generates first-principles claims"
        ),
        LlmAgent(
            name="tp_student",
            model="gemini-3-flash-preview",
            instruction=STUDENT_TEMPLATE,
            output_schema=StudentAttacksSchema,
            output_key="student_challenges",
            description="Stress-tests professor claims"
        ),
        LlmAgent(
            name="tp_synthesis",
            model="gemini-3-pro-preview",
            instruction=SYNTHESIS_TEMPLATE,
            output_schema=SynthesisClaimsSchema,
            output_key="synthesis_claims",
            description="Reconciles claims and challenges"
        ),
        LlmAgent(
            name="tp_verifier",
            model="gemini-3-flash-preview",
            instruction=VERIFIER_TEMPLATE,
            output_schema=VerifierOutputSchema,
            output_key="verifier_report",
            description="Audits claims against evidence"
        ),
        # Phase A: Generate outline instead of full script
        LlmAgent(
            name="tp_outline_generator",
            model="gemini-3-pro-preview",
            instruction=OUTLINE_GENERATOR_TEMPLATE,
            output_schema=ScriptOutlineSchema,
            output_key="script_outline",
            description="Generates 25-30 scene outline"
        ),
    ],
    description="Two-phase pipeline: Phase A generates outline, Phase B (scene expansion) handled by CLI"
)
