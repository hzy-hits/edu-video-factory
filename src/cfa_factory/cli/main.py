from __future__ import annotations

# Load environment variables BEFORE importing agents (for DeepSeek API key)
from dotenv import load_dotenv
load_dotenv()

from pathlib import Path
import json
import re
import os
import typer
from loguru import logger
import asyncio
import chromadb

from cfa_factory.tools.manifest import load_manifest, load_reading_map
from cfa_factory.tools.chunker import build_chunks_for_doc
from cfa_factory.tools.index_store import build_chroma_index
from cfa_factory.tools.retrieval import build_evidence_packet
from cfa_factory.tools.vision_extract import process_vision_for_reading_async
from cfa_factory.tools.reading_map_builder import build_reading_map_for_doc

# Google ADK imports
from cfa_factory.agents.framework import InMemorySessionService, Runner, LlmAgent, DeepSeekAgent
from cfa_factory.agents.schemas import VideoScriptSchema
from cfa_factory.agents.prompts import DEEPSEEK_TRANSLATOR_TEMPLATE
from cfa_factory.agents.core import (
    debate_pipeline, 
    multi_round_debate_pipeline, 
    production_pipeline,
    production_pipeline_en,
    two_phase_pipeline,
    scene_expander,
    script_translator,  # Phase C: DeepSeek translation
)


app = typer.Typer(help="CFA Factory CLI (M1: indexing & evidence packet)")
vision_app = typer.Typer(help="Vision-on-Demand tools")
app.add_typer(vision_app, name="vision")

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "assets"
OUT = ROOT / "output"
DEFAULT_CONFIG_PATH = ROOT / "config" / "cfa.yaml"


def _normalize_reading_id(value: str) -> str:
    raw = (value or "").strip()
    match = re.search(r"(\d+)", raw)
    if match:
        return str(int(match.group(1)))
    return raw


def _has_chroma_collection(chroma_dir: Path, name: str) -> bool:
    if not chroma_dir.exists():
        return False
    try:
        client = chromadb.PersistentClient(path=str(chroma_dir))
        cols = client.list_collections()
        return any(c.name == name for c in cols)
    except Exception as exc:
        logger.warning(f"Failed to list collections in {chroma_dir}: {exc}")
        return False


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning(f"Ignoring invalid int for {name}: {value}")
        return None


def _load_yaml_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data: dict[str, object] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, raw = stripped.split(":", 1)
        key = key.strip()
        value = raw.strip().strip('"').strip("'")
        if value.lower() in {"true", "false"}:
            data[key] = value.lower() == "true"
        else:
            try:
                data[key] = int(value)
            except ValueError:
                data[key] = value
    return data


@app.command("chunk")
def chunk(
    doc: str = typer.Option(..., "--doc", help="doc_id in manifest"),
    force: bool = typer.Option(False, "--force", help="Force re-chunk even if file exists"),
    llm: bool = typer.Option(True, "--llm/--no-llm", help="Use LLM block chunker"),
    parallel: int = typer.Option(1, "--parallel", help="LLM chunking parallelism"),
    llm_mode: str = typer.Option("all", "--llm-mode", help="LLM mode: all | vision-only | off"),
    llm_model: str = typer.Option("gemini-2.5-flash-lite", "--llm-model", help="Base LLM for chunking"),
    llm_model_complex: str = typer.Option("gemini-3-flash-preview", "--llm-model-complex", help="LLM for complex pages"),
    manifest_path: Path = ASSETS / "manifest.json",
    reading_map_path: Path = ASSETS / "reading_map.json"
):
    """
    Chunk a PDF into JSONL files.
    Use --force to rebuild chunks even if they already exist.
    """
    out_dir = OUT / "index" / "chunks"
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_file = out_dir / f"{doc}.jsonl"
    
    # Skip if already exists and not forced
    if chunks_file.exists() and not force:
        # Count existing chunks
        with chunks_file.open() as f:
            count = sum(1 for _ in f)
        logger.info(f"Chunks already exist: {chunks_file} ({count} chunks)")
        logger.info("Use --force to rebuild")
        return
    
    manifest = load_manifest(manifest_path)
    doc_entry = next((d for d in manifest if d.doc_id == doc), None)
    if not doc_entry:
        raise ValueError(f"doc_id={doc} not in manifest")

    pdf_path = ASSETS / doc_entry.path
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    reading_map = load_reading_map(reading_map_path)

    result_file = build_chunks_for_doc(
        pdf_path=pdf_path,
        doc_id=doc,
        kind=doc_entry.kind,
        reading_map=reading_map,
        out_dir=out_dir,
        use_llm=llm,
        parallel=parallel,
        llm_mode=llm_mode,
        llm_model=llm_model,
        llm_model_complex=llm_model_complex
    )

    logger.info(f"Chunks saved to {result_file}")


@app.command("index")
def index(doc: str = typer.Option(..., "--doc", help="doc_id")):
    """
    Build ChromaDB index from JSONL chunks.
    """
    chunks_file = OUT / "index" / "chunks" / f"{doc}.jsonl"
    db_path = OUT / "index" / "chroma" / doc
    build_chroma_index(str(chunks_file), str(db_path))
    logger.info(f"Index built: {db_path}")


@app.command("index-all")
def index_all():
    """
    Build UNIFIED ChromaDB index from ALL chunk files.
    This creates a cross-book searchable index for RAG.
    """
    chunks_dir = OUT / "index" / "chunks"
    unified_db = OUT / "index" / "chroma" / "unified"
    
    # Count chunks before indexing
    chunk_files = list(chunks_dir.glob("*.jsonl"))
    logger.info(f"Found {len(chunk_files)} chunk files")
    
    build_chroma_index(chunks_dir, unified_db)
    logger.info(f"Unified index built: {unified_db}")


@app.command("packet")
def packet(
    doc: str = typer.Option(..., "--doc", help="doc_id"),
    reading: str = typer.Option(..., "--reading", help="reading_id"),
    cross_ref: bool = typer.Option(False, "--cross-ref", help="Enable cross-book RAG from unified index")
):
    """
    Build evidence packet.
    Use --cross-ref to include Schweser and cross-volume references.
    """
    reading_norm = _normalize_reading_id(reading)
    chunks_file = OUT / "index" / "chunks" / f"{doc}.jsonl"
    db_path = OUT / "index" / "chroma" / doc
    unified_db = OUT / "index" / "chroma" / "unified"
    runs_dir = OUT / "runs"
    
    ep = build_evidence_packet(
        doc, reading_norm, chunks_file, db_path, runs_dir,
        cross_ref=cross_ref,
        unified_chroma_dir=unified_db if cross_ref else None
    )
    logger.info(f"Evidence packet created: {ep.run_id}" + (" (with cross-ref)" if cross_ref else ""))


@app.command("reading-map")
def reading_map(
    doc: str = typer.Option(..., "--doc", help="doc_id"),
    max_pages: int | None = typer.Option(None, "--max-pages", help="Limit scan to first N pages"),
    toc_pages: int = typer.Option(25, "--toc-pages", help="Pages to feed TOC extractor"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable LLM confirmation"),
    debug: bool = typer.Option(False, "--debug", help="Log candidate pages and LLM calls"),
    manifest_path: Path = ASSETS / "manifest.json",
    out_path: Path = ASSETS / "reading_map.json"
):
    """
    Build or update reading_map.json by detecting reading start pages.
    Uses Gemini to confirm reading_id/title unless --no-llm is set.
    """
    build_reading_map_for_doc(
        doc_id=doc,
        manifest_path=manifest_path,
        out_path=out_path,
        use_llm=not no_llm,
        max_pages=max_pages,
        toc_pages=toc_pages,
        debug=debug
    )


@vision_app.command("extract")
def vision_extract(
    doc: str = typer.Option(..., "--doc", help="doc_id"),
    reading: str = typer.Option(..., "--reading", help="reading_id"),
    model: str = typer.Option("gemini-3-flash-preview", "--model", help="Vision model"),
    formula_only: bool = typer.Option(False, "--formula-only", help="Extract formulas only"),
    filter_mode: str = typer.Option("all", "--filter-mode", help="all | formula-pages"),
    manifest_path: Path = ASSETS / "manifest.json",
    reading_map_path: Path = ASSETS / "reading_map.json"
):
    """
    Extract structured assets using Gemini Vision.
    """
    manifest = load_manifest(manifest_path)
    doc_entry = next((d for d in manifest if d.doc_id == doc), None)
    if not doc_entry:
        raise ValueError(f"doc_id={doc} not in manifest")

    pdf_path = ASSETS / doc_entry.path
    reading_norm = _normalize_reading_id(reading)
    reading_map = load_reading_map(reading_map_path)
    doc_map = reading_map.get(doc, [])
    pages_range = next((r for r in doc_map if str(r.get("reading_id")) == reading_norm), None)
    if not pages_range:
        raise ValueError(f"Reading {reading} not found for {doc}")
    page_start = int(pages_range["page_start"])
    page_end = int(pages_range["page_end"])
    pages_range = range(page_start, page_end + 1)

    chunks_dir = OUT / "index" / "chunks"
    assets_out_dir = OUT / "vision_assets" / doc / reading_norm

    asyncio.run(process_vision_for_reading_async(
        pdf_path=pdf_path,
        doc_id=doc,
        reading_id=reading_norm,
        pages_range=pages_range,
        chunks_dir=chunks_dir,
        assets_out_dir=assets_out_dir,
        kind=doc_entry.kind,
        model=model,
        formula_only=formula_only,
        filter_mode=filter_mode
    ))


@app.command("run")
def run(
    doc: str = typer.Option(..., "--doc", help="doc_id"),
    reading: str = typer.Option(..., "--reading", help="reading_id"),
    cross_ref: bool = typer.Option(False, "--cross-ref", help="Enable cross-book RAG from unified index"),
    prep: bool = typer.Option(False, "--prep", help="Auto-build missing indexes before packet/run"),
    min_claims: int = typer.Option(None, "--min-claims", help="Minimum professor claims target"),
    min_scenes: int = typer.Option(None, "--min-scenes", help="Minimum scene count target"),
    min_scene_words: int = typer.Option(None, "--min-scene-words", help="Minimum English words per scene"),
    smooth_zh: bool | None = typer.Option(None, "--smooth-zh/--no-smooth-zh", help="Polish spoken_zh for coherence after translation"),
    smooth_window: int = typer.Option(None, "--smooth-window", help="Context window (prev/next scenes) used for Chinese smoothing"),
    skip_translate: bool = typer.Option(False, "--skip-translate/--with-translate", help="Skip translation step; output English only"),
    multi_round: bool = typer.Option(False, "--multi-round", help="Force multi-round debate"),
    with_editor: bool = typer.Option(False, "--with-editor", help="Include Editor for video script generation"),
    two_phase: bool = typer.Option(False, "--two-phase", help="Two-phase generation: outline then expand each scene"),
    auto: bool = typer.Option(False, "--auto", help="Let Router decide depth (single vs multi-round)"),
    runs_dir: Path = OUT / "runs"
):
    """
    Execute the M2 Agentic Workflow using Google ADK.
    
    Pipeline Options:
    - Default: Router -> Professor -> Student -> Synthesis -> Verifier
    - --with-editor: Adds Editor + Continuity Gate for video script
    - --multi-round: Multi-round debate for deeper content
    - --two-phase: Two-phase generation (25-30 scenes * 250 words = 40+ minute video)
    - --cross-ref: Use unified index for cross-book RAG when building packet
    - --prep: Auto-build missing indexes before packet/run
    
    Depth Control:
    - Default: Single-round debate
    - --multi-round: Force multi-round (for 2-4 hour content)
    - --auto: Let Router decide based on content complexity
    """
    # 1. Locate or build evidence packet
    reading_norm = _normalize_reading_id(reading)
    doc_run_root = runs_dir / doc / reading_norm
    packet_data = None
    top_run_id = None
    output_dir = None

    if doc_run_root.exists():
        run_ids = sorted([d.name for d in doc_run_root.iterdir() if d.is_dir()])
        if run_ids:
            candidate = doc_run_root / run_ids[-1] / "evidence_packet.json"
            if candidate.exists():
                top_run_id = run_ids[-1]
                output_dir = doc_run_root / top_run_id
                logger.info(f"Loading evidence from: {candidate}")
                packet_data = json.loads(candidate.read_text())

    if packet_data is None:
        chunks_file = OUT / "index" / "chunks" / f"{doc}.jsonl"
        db_path = OUT / "index" / "chroma" / doc
        unified_db = OUT / "index" / "chroma" / "unified"

        if prep:
            if not chunks_file.exists():
                raise FileNotFoundError(
                    f"Missing chunks file: {chunks_file}. "
                    f"Run: uv run cfa chunk --doc {doc} --llm"
                )
            if not _has_chroma_collection(db_path, "cfa_chunks"):
                logger.info("Doc index missing; building index...")
                build_chroma_index(chunks_file, db_path)
            if cross_ref and not _has_chroma_collection(unified_db, "cfa_chunks"):
                logger.info("Unified index missing; building index-all...")
                build_chroma_index(OUT / "index" / "chunks", unified_db)

        logger.info("No evidence packet found; building a new one...")
        ep = build_evidence_packet(
            doc,
            reading_norm,
            chunks_file,
            db_path,
            runs_dir,
            cross_ref=cross_ref,
            unified_chroma_dir=unified_db if cross_ref else None,
        )
        top_run_id = ep.run_id
        output_dir = doc_run_root / top_run_id
        packet_path = output_dir / "evidence_packet.json"
        logger.info(f"Loading evidence from: {packet_path}")
        packet_data = json.loads(packet_path.read_text())

    if output_dir is None or top_run_id is None:
        raise FileNotFoundError(f"No run data found for {doc}/{reading}")

    # 2b. Build a short reading summary from the first few chunks
    summary_parts = []
    for ch in packet_data.get("reading_fulltext", [])[:3]:
        content = (ch.get("content") or "").strip()
        if content:
            summary_parts.append(content)
    reading_summary = " ".join(summary_parts)
    if len(reading_summary) > 1000:
        reading_summary = reading_summary[:1000]
    target_minutes = 0
    chunk_count = len(packet_data.get("reading_fulltext", []))
    config_path = Path(os.getenv("CFA_CONFIG", DEFAULT_CONFIG_PATH))
    cfg = _load_yaml_config(config_path)

    base_min_claims = max(40, min(120, chunk_count // 2)) if chunk_count else 40
    env_min_claims = _env_int("CFA_MIN_CLAIMS")
    cfg_min_claims = cfg.get("min_claims")
    min_claims_target = (
        min_claims
        if min_claims is not None
        else (
            cfg_min_claims
            if isinstance(cfg_min_claims, int)
            else (env_min_claims if env_min_claims is not None else base_min_claims)
        )
    )
    base_min_scenes = max(48, min(120, int(min_claims_target * 1.2)))
    env_min_scenes = _env_int("CFA_MIN_SCENES")
    cfg_min_scenes = cfg.get("min_scenes")
    min_scene_count = (
        min_scenes
        if min_scenes is not None
        else (
            cfg_min_scenes
            if isinstance(cfg_min_scenes, int)
            else (env_min_scenes if env_min_scenes is not None else base_min_scenes)
        )
    )
    env_min_scene_words = _env_int("CFA_MIN_SCENE_WORDS")
    cfg_min_scene_words = cfg.get("min_scene_words")
    if min_scene_words is None:
        if isinstance(cfg_min_scene_words, int):
            min_scene_words = cfg_min_scene_words
        else:
            min_scene_words = env_min_scene_words if env_min_scene_words is not None else 80
    min_scene_words = max(40, min_scene_words)

    cfg_smooth_zh = cfg.get("smooth_zh")
    if smooth_zh is None:
        smooth_zh = cfg_smooth_zh if isinstance(cfg_smooth_zh, bool) else True

    cfg_smooth_window = cfg.get("smooth_window")
    if smooth_window is None:
        smooth_window = cfg_smooth_window if isinstance(cfg_smooth_window, int) else 1
    smooth_window = max(0, int(smooth_window))

    # 2. Prepare initial state (flat keys for ADK template substitution)
    initial_state = {
        "doc_id": doc,
        "reading_id": reading_norm,
        "reading_title": f"{doc} Reading {reading_norm}",
        "reading_summary": reading_summary,
        "chunk_count": chunk_count,
        "book_spine": "Book Spine Placeholder",
        "book_glossary": json.dumps({"term_map": {}, "symbol_map": {}}, ensure_ascii=False),
        "lesson_evidence_packet": json.dumps(packet_data, ensure_ascii=False),
        "lesson_plan_mode": "",  # Will be set by Router
        "search_context": "",
        "lecture_outline": "",
        "target_minutes": target_minutes,
        "min_claims_target": min_claims_target,
        "min_scene_count": min_scene_count,
        "min_scene_words": min_scene_words,
        "smooth_zh": smooth_zh,
        "smooth_window": smooth_window,
        # Debate outputs - Round 1 (will be set by agents)
        "professor_claims": "",
        "student_challenges": "",
        "synthesis_claims": "",
        # Debate outputs - Round 2 Deep-Dive (for multi-round mode)
        "professor_claims_deepdive": "",
        "student_challenges_deepdive": "",
        # Verification
        "verifier_report": "",
        # Editor outputs (for --with-editor mode)
        "editor_raw": "",
        "professor_lecture": "",
        "english_script": "",
        "translated_raw": "",
        "editor_script": "",
        "continuity_report": "",
    }

    # 3. Create ADK Session Service
    session_service = InMemorySessionService()

    # 4. Select Pipeline
    if two_phase:
        pipeline = two_phase_pipeline
        pipeline_mode = "Two-Phase (Outline + Expansion)"
    elif with_editor:
        if skip_translate:
            pipeline = production_pipeline_en
            pipeline_mode = "Production (English-only)"
        else:
            pipeline = production_pipeline
            pipeline_mode = "Production (with Translator)"
    elif multi_round:
        pipeline = multi_round_debate_pipeline
        pipeline_mode = "Multi-Round"
    else:
        pipeline = debate_pipeline
        pipeline_mode = "Single-Round"
    
    # 5. Create Runner
    runner = Runner(
        agent=pipeline,
        app_name="cfa_factory",
        session_service=session_service
    )

    # 6. Execute workflow (all async operations inside one async function)
    logger.info("Starting M2 Agentic Workflow...")
    logger.info(f"Pipeline: {pipeline.name} ({pipeline_mode})")
    
    from google.genai import types
    
    async def run_pipeline_safe():
        session = await session_service.create_session(
            app_name="cfa_factory",
            user_id="cli_user",
            state=initial_state
        )
        run_error: Exception | None = None
        try:
            async for event in runner.run_async(
                user_id="cli_user",
                session_id=session.id,
                new_message=types.Content(parts=[types.Part(text="Execute the debate pipeline.")])
            ):
                logger.debug(f"Event from [{event.author}]: {event.content}")
        except Exception as exc:
            run_error = exc
            logger.error(f"Pipeline failed: {exc}")
        final_session = await session_service.get_session(
            app_name="cfa_factory",
            user_id="cli_user",
            session_id=session.id
        )
        return final_session.state, run_error

    final_state, error = asyncio.run(run_pipeline_safe())

    # 7. Save results
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    state_path.write_text(json.dumps(dict(final_state), indent=2, ensure_ascii=False))
    logger.info(f"Workflow completed! State saved to {state_path}")
    
    # 8. Save Editor output separately (if --with-editor)
    if with_editor and "professor_lecture" in final_state and final_state["professor_lecture"]:
        lecture_path = output_dir / "professor_lecture.json"
        lecture_value = final_state["professor_lecture"]
        if isinstance(lecture_value, str):
            try:
                lecture_value = json.loads(lecture_value)
            except Exception:
                lecture_value = {"raw": lecture_value}
        lecture_path.write_text(json.dumps(lecture_value, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Professor lecture draft saved to {lecture_path}")

    if with_editor and "english_script" in final_state and final_state["english_script"]:
        en_path = output_dir / "english_script.json"
        en_value = final_state["english_script"]
        if isinstance(en_value, str):
            try:
                en_value = json.loads(en_value)
            except Exception:
                en_value = {"raw": en_value}
        en_path.write_text(json.dumps(en_value, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"English script saved to {en_path}")

    if with_editor and "translated_raw" in final_state and final_state["translated_raw"]:
        tr_path = output_dir / "translated_raw.txt"
        tr_value = final_state["translated_raw"]
        if isinstance(tr_value, (dict, list)):
            tr_path.write_text(json.dumps(tr_value, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            tr_path.write_text(str(tr_value), encoding="utf-8")
        logger.info(f"Translated raw output saved to {tr_path}")


    if with_editor and "editor_script" in final_state and final_state["editor_script"]:
        script_data = final_state["editor_script"]
        if isinstance(script_data, str):
            script_data = json.loads(script_data)
        
        # Save JSON version
        script_path = output_dir / "video_script.json"
        script_path.write_text(json.dumps(script_data, indent=2, ensure_ascii=False))
        logger.info(f"Video script saved to {script_path}")
        
        # Save human-readable Chinese version
        if "scenes" in script_data:
            script_txt = output_dir / "script_zh.txt"
            with script_txt.open("w", encoding="utf-8") as f:
                f.write(f"# 视频脚本: {script_data.get('segment_id', 'Unknown')}\n")
                f.write(f"# 预计时长: {script_data.get('duration_est_min', '?')} 分钟\n\n")
                for i, scene in enumerate(script_data["scenes"], 1):
                    f.write(f"## Scene {i}: {scene.get('beat', 'Unknown')}\n")
                    f.write(f"【画面】{scene.get('display_zh', '')}\n")
                    speaker_en = scene.get('speaker', 'Narrator')
                    speaker_map = {"Professor": "教授", "Student": "学员", "Narrator": "旁白"}
                    speaker_label = speaker_map.get(speaker_en, speaker_en)
                    f.write(f"【{speaker_label}】{scene.get('spoken_zh', '')}\n")
                    if scene.get("citations"):
                        f.write(f"【引用】{', '.join(scene['citations'])}\n")
                    f.write("\n")
            logger.info(f"Human-readable script saved to {script_txt}")
    elif with_editor:
        logger.warning("Editor script missing/empty; only state and editor_raw (if any) were saved.")

    if error:
        raise error


@app.command("translate")
def translate(
    doc: str = typer.Option(None, "--doc", help="doc_id"),
    reading: str = typer.Option(None, "--reading", help="reading_id"),
    run_id: str = typer.Option(None, "--run-id", help="run_id to translate"),
    english_path: Path = typer.Option(None, "--english", help="path to english_script.json"),
    fallback: bool = typer.Option(False, "--fallback", help="Force Gemini fallback translator"),
    max_retries: int = typer.Option(5, "--max-retries", help="DeepSeek retry attempts"),
    per_scene: bool = typer.Option(True, "--per-scene/--whole", help="Translate per scene to avoid JSON truncation"),
    parallel: int = typer.Option(1, "--parallel", help="Per-scene translation parallelism"),
    smooth_zh: bool | None = typer.Option(None, "--smooth-zh/--no-smooth-zh", help="Polish spoken_zh for coherence"),
    smooth_window: int = typer.Option(None, "--smooth-window", help="Context window for Chinese smoothing"),
):
    """
    Translate an existing english_script.json to Chinese without rerunning the pipeline.
    """
    if english_path is None:
        if not doc or not reading:
            raise typer.BadParameter("Provide --english or both --doc and --reading.")
        reading_norm = _normalize_reading_id(reading)
        run_root = OUT / "runs" / doc / reading_norm
        if not run_root.exists():
            raise FileNotFoundError(f"No runs found for {doc}/{reading_norm}")
        if run_id:
            candidate = run_root / run_id / "english_script.json"
            if not candidate.exists():
                raise FileNotFoundError(f"Missing english_script.json at {candidate}")
            english_path = candidate
        else:
            runs = sorted([d for d in run_root.iterdir() if d.is_dir()], key=lambda p: p.name)
            found = None
            for r in reversed(runs):
                cand = r / "english_script.json"
                if cand.exists():
                    found = cand
                    break
            if not found:
                raise FileNotFoundError(f"No english_script.json found under {run_root}")
            english_path = found

    english_path = Path(english_path)
    output_dir = english_path.parent
    english_script = json.loads(english_path.read_text(encoding="utf-8"))
    config_path = Path(os.getenv("CFA_CONFIG", DEFAULT_CONFIG_PATH))
    cfg = _load_yaml_config(config_path)
    cfg_smooth_zh = cfg.get("smooth_zh")
    if smooth_zh is None:
        smooth_zh = cfg_smooth_zh if isinstance(cfg_smooth_zh, bool) else True
    cfg_smooth_window = cfg.get("smooth_window")
    if smooth_window is None:
        smooth_window = cfg_smooth_window if isinstance(cfg_smooth_window, int) else 1
    smooth_window = max(0, int(smooth_window))

    use_deepseek = (not fallback) and bool(os.getenv("DEEPSEEK_API_KEY"))
    if per_scene and not use_deepseek:
        raise typer.BadParameter("--per-scene requires DEEPSEEK_API_KEY or remove --per-scene.")
    if per_scene:
        import openai

        def translate_scene(client, scene, prev_spoken, next_spoken, attempt_limit):
            base_prompt = (
                "You are a professional translator. Translate ONLY the content. "
                "Do NOT add or remove facts. Keep LaTeX unchanged.\n"
                "Style: natural spoken Mandarin for teaching. Avoid literal translation. "
                "You may rephrase for fluency while preserving meaning.\n"
                "Prefer short sentences, clear logic, and oral connectors (e.g., 先/再/所以/但注意).\n"
                "Avoid stiff translationese (avoid '因此/从而/由于' overuse). Keep tone conversational.\n"
                "Context (do NOT translate; use only for coherence and terminology consistency):\n"
                f"PREV_SPOKEN_EN: {prev_spoken}\n"
                f"NEXT_SPOKEN_EN: {next_spoken}\n"
                "Return exactly two lines:\n"
                "DISPLAY_ZH: ...\n"
                "SPOKEN_ZH: ...\n"
                "No extra text.\n\n"
                f"SPEAKER: {scene.get('speaker','Narrator')}\n"
                f"DISPLAY_EN: {scene.get('display_en','')}\n"
                f"SPOKEN_EN: {scene.get('spoken_en','')}\n"
            )
            last_error = None
            for attempt in range(attempt_limit):
                resp = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": base_prompt}],
                    stream=False
                )
                content = resp.choices[0].message.content.strip()
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                display = ""
                spoken = ""
                for line in lines:
                    if line.startswith("DISPLAY_ZH:"):
                        display = line[len("DISPLAY_ZH:"):].strip()
                    elif line.startswith("SPOKEN_ZH:"):
                        spoken = line[len("SPOKEN_ZH:"):].strip()
                if display and spoken:
                    return display, spoken, content
                last_error = content
            raise ValueError(f"DeepSeek per-scene translation failed. Last output: {last_error}")

        client = openai.Client(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )
        from concurrent.futures import ThreadPoolExecutor, as_completed

        scenes = english_script.get("scenes", [])
        total_scenes = len(scenes)
        translated_scenes = [None] * total_scenes
        raw_outputs = [None] * total_scenes

        def _translate_one(i_scene):
            idx, scene = i_scene
            logger.info(f"Translating scene {idx+1}/{total_scenes}...")
            prev_spoken = scenes[idx - 1].get("spoken_en", "") if idx > 0 else ""
            next_spoken = scenes[idx + 1].get("spoken_en", "") if idx + 1 < total_scenes else ""
            display_zh, spoken_zh, raw = translate_scene(client, scene, prev_spoken, next_spoken, max_retries)
            result = {
                "beat": scene.get("beat"),
                "speaker": scene.get("speaker", "Narrator"),
                "display_zh": display_zh,
                "spoken_zh": spoken_zh,
                "citations": scene.get("citations", []),
                "visual_refs": scene.get("visual_refs", []),
                "quiz": scene.get("quiz")
            }
            return idx, result, raw

        workers = max(1, min(parallel, 8))
        with ThreadPoolExecutor(max_workers=workers) as exe:
            futures = [exe.submit(_translate_one, (i, s)) for i, s in enumerate(scenes)]
            done = 0
            for fut in as_completed(futures):
                idx, result, raw = fut.result()
                translated_scenes[idx] = result
                raw_outputs[idx] = {"scene": idx + 1, "translate_raw": raw, "smooth_raw": None}
                done += 1
                if done % 5 == 0 or done == total_scenes:
                    logger.info(f"Translated {done}/{total_scenes} scenes")

        if smooth_zh and translated_scenes:
            context_spoken = [s.get("spoken_zh", "") for s in translated_scenes]

            def smooth_scene(client, scene_idx, attempt_limit):
                scene = translated_scenes[scene_idx]
                original = scene.get("spoken_zh", "")
                if not original:
                    return scene_idx, None, None
                prev_ctx = "\n".join(
                    s for s in context_spoken[max(0, scene_idx - smooth_window):scene_idx] if s
                )
                next_ctx = "\n".join(
                    s for s in context_spoken[scene_idx + 1:scene_idx + 1 + smooth_window] if s
                )
                smooth_prompt = (
                    "You are a Chinese dialogue editor. Polish ONLY the current SPOKEN_ZH.\n"
                    "Do NOT add or remove facts. Keep meaning intact.\n"
                    "Use context for smoother transitions, but do not introduce new information.\n"
                    "Keep length roughly similar (±20%). Preserve key terms/abbreviations.\n"
                    "Return exactly one line:\n"
                    "SPOKEN_ZH: ...\n"
                    "No extra text.\n\n"
                    f"SPEAKER: {scene.get('speaker','Narrator')}\n"
                    f"PREV_CONTEXT: {prev_ctx}\n"
                    f"NEXT_CONTEXT: {next_ctx}\n"
                    f"CURRENT_SPOKEN_ZH: {original}\n"
                )
                last_output = None
                for _ in range(attempt_limit):
                    resp = client.chat.completions.create(
                        model="deepseek-chat",
                        messages=[{"role": "user", "content": smooth_prompt}],
                        stream=False
                    )
                    content = resp.choices[0].message.content.strip()
                    last_output = content
                    for line in content.splitlines():
                        line = line.strip()
                        if line.startswith("SPOKEN_ZH:"):
                            candidate = line[len("SPOKEN_ZH:"):].strip()
                            ratio = len(candidate) / max(1, len(original))
                            if 0.7 <= ratio <= 1.3:
                                return scene_idx, candidate, last_output
                return scene_idx, None, last_output

            workers = max(1, min(parallel, 8))
            with ThreadPoolExecutor(max_workers=workers) as exe:
                futures = [exe.submit(smooth_scene, client, i, max_retries) for i in range(total_scenes)]
                done = 0
                for fut in as_completed(futures):
                    idx, candidate, raw = fut.result()
                    if candidate:
                        translated_scenes[idx]["spoken_zh"] = candidate
                    if raw_outputs[idx] is None:
                        raw_outputs[idx] = {"scene": idx + 1, "translate_raw": None, "smooth_raw": raw}
                    else:
                        raw_outputs[idx]["smooth_raw"] = raw
                    done += 1
                    if done % 5 == 0 or done == total_scenes:
                        logger.info(f"Smoothed {done}/{total_scenes} scenes")

        script_data = {
            "segment_id": english_script.get("segment_id", "segment"),
            "duration_est_min": english_script.get("duration_est_min"),
            "scenes": translated_scenes
        }

        raw_path = output_dir / "translated_raw.txt"
        raw_path.write_text(json.dumps(raw_outputs, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Translated raw output saved to {raw_path}")

        script_path = output_dir / "video_script.json"
        script_path.write_text(json.dumps(script_data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Video script saved to {script_path}")

        if "scenes" in script_data:
            script_txt = output_dir / "script_zh.txt"
            with script_txt.open("w", encoding="utf-8") as f:
                f.write(f"# 视频脚本: {script_data.get('segment_id', 'Unknown')}\n")
                f.write(f"# 预计时长: {script_data.get('duration_est_min', '?')} 分钟\n\n")
                for i, scene in enumerate(script_data["scenes"], 1):
                    f.write(f"## Scene {i}: {scene.get('beat', 'Unknown')}\n")
                    f.write(f"【画面】{scene.get('display_zh', '')}\n")
                    speaker_en = scene.get('speaker', 'Narrator')
                    speaker_map = {"Professor": "教授", "Student": "学员", "Narrator": "旁白"}
                    speaker_label = speaker_map.get(speaker_en, speaker_en)
                    f.write(f"【{speaker_label}】{scene.get('spoken_zh', '')}\n")
                    if scene.get("citations"):
                        f.write(f"【引用】{', '.join(scene['citations'])}\n")
                    f.write("\n")
            logger.info(f"Human-readable script saved to {script_txt}")
        return

    if use_deepseek:
        translator = DeepSeekAgent(
            name="translator_only",
            deepseek_model="deepseek-chat",
            base_url="https://api.deepseek.com",
            instruction=DEEPSEEK_TRANSLATOR_TEMPLATE,
            output_schema=VideoScriptSchema,
            output_key="editor_script",
            max_retries=max_retries,
            raw_output_key="translated_raw",
            save_raw_always=True,
            description="Translates English script to Chinese using DeepSeek (strict JSON output)"
        )
    else:
        translator = LlmAgent(
            name="translator_fallback_only",
            model="gemini-3-flash-preview",
            instruction=DEEPSEEK_TRANSLATOR_TEMPLATE,
            output_schema=VideoScriptSchema,
            output_key="editor_script",
            description="Fallback translator to Chinese (strict JSON output)"
        )

    session_service = InMemorySessionService()
    runner = Runner(
        agent=translator,
        app_name="cfa_factory",
        session_service=session_service
    )

    from google.genai import types

    async def run_translate_safe():
        session = await session_service.create_session(
            app_name="cfa_factory",
            user_id="cli_user",
            state={"english_script": json.dumps(english_script, ensure_ascii=False)}
        )
        run_error: Exception | None = None
        try:
            async for event in runner.run_async(
                user_id="cli_user",
                session_id=session.id,
                new_message=types.Content(parts=[types.Part(text="Translate the script.")])
            ):
                logger.debug(f"Event from [{event.author}]: {event.content}")
        except Exception as exc:
            run_error = exc
            logger.error(f"Translation failed: {exc}")
        final_session = await session_service.get_session(
            app_name="cfa_factory",
            user_id="cli_user",
            session_id=session.id
        )
        return final_session.state, run_error

    final_state, error = asyncio.run(run_translate_safe())

    output_dir.mkdir(parents=True, exist_ok=True)

    if "translated_raw" in final_state and final_state["translated_raw"]:
        tr_path = output_dir / "translated_raw.txt"
        tr_value = final_state["translated_raw"]
        if isinstance(tr_value, (dict, list)):
            tr_path.write_text(json.dumps(tr_value, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            tr_path.write_text(str(tr_value), encoding="utf-8")
        logger.info(f"Translated raw output saved to {tr_path}")

    if "editor_script" in final_state and final_state["editor_script"]:
        script_data = final_state["editor_script"]
        if isinstance(script_data, str):
            script_data = json.loads(script_data)

        script_path = output_dir / "video_script.json"
        script_path.write_text(json.dumps(script_data, indent=2, ensure_ascii=False))
        logger.info(f"Video script saved to {script_path}")

        if "scenes" in script_data:
            script_txt = output_dir / "script_zh.txt"
            with script_txt.open("w", encoding="utf-8") as f:
                f.write(f"# 视频脚本: {script_data.get('segment_id', 'Unknown')}\n")
                f.write(f"# 预计时长: {script_data.get('duration_est_min', '?')} 分钟\n\n")
                for i, scene in enumerate(script_data["scenes"], 1):
                    f.write(f"## Scene {i}: {scene.get('beat', 'Unknown')}\n")
                    f.write(f"【画面】{scene.get('display_zh', '')}\n")
                    speaker_en = scene.get('speaker', 'Narrator')
                    speaker_map = {"Professor": "教授", "Student": "学员", "Narrator": "旁白"}
                    speaker_label = speaker_map.get(speaker_en, speaker_en)
                    f.write(f"【{speaker_label}】{scene.get('spoken_zh', '')}\n")
                    if scene.get("citations"):
                        f.write(f"【引用】{', '.join(scene['citations'])}\n")
                    f.write("\n")
            logger.info(f"Human-readable script saved to {script_txt}")

    if error:
        raise error
        # Log continuity report
        if "continuity_report" in final_state and final_state["continuity_report"]:
            cont_report = final_state["continuity_report"]
            if isinstance(cont_report, str):
                cont_report = json.loads(cont_report)
            if cont_report.get("passed"):
                logger.info("✅ Continuity check PASSED")
            else:
                logger.warning(f"⚠️ Continuity issues: {cont_report.get('issues', [])}")
    
    # 8b. Two-Phase Mode: Phase B Scene Expansion
    if two_phase and "script_outline" in final_state and final_state["script_outline"]:
        outline_data = final_state["script_outline"]
        if isinstance(outline_data, str):
            outline_data = json.loads(outline_data)
        
        logger.info(f"📋 Phase A complete: {len(outline_data.get('scenes', []))} scenes in outline")
        logger.info("🔄 Starting Phase B: Expanding each scene...")
        
        from google.genai import types as genai_types
        
        async def expand_all_scenes():
            """Phase B: Expand each scene sequentially"""
            expanded_scenes = []
            scenes = outline_data.get("scenes", [])
            
            for i, scene_outline in enumerate(scenes):
                logger.info(f"  Expanding scene {i+1}/{len(scenes)}: {scene_outline.get('title_zh', 'Unknown')}")
                
                # Create a new session for each scene expansion
                scene_session = await session_service.create_session(
                    app_name="cfa_factory",
                    user_id="cli_user",
                    state={
                        "current_scene": json.dumps(scene_outline, ensure_ascii=False),
                        "prev_scenes": json.dumps(expanded_scenes[-3:] if expanded_scenes else [], ensure_ascii=False),
                        "lesson_evidence_packet": json.dumps(packet_data, ensure_ascii=False),
                    }
                )
                
                # Create runner for scene_expander
                scene_runner = Runner(
                    agent=scene_expander,
                    app_name="cfa_factory",
                    session_service=session_service
                )
                
                # Run scene expansion
                async for event in scene_runner.run_async(
                    user_id="cli_user",
                    session_id=scene_session.id,
                    new_message=genai_types.Content(parts=[genai_types.Part(text="Expand this scene into detailed dialogue.")])
                ):
                    pass  # Process events silently
                
                # Get expanded scene from session
                expanded_session = await session_service.get_session(
                    app_name="cfa_factory",
                    user_id="cli_user",
                    session_id=scene_session.id
                )
                
                expanded_scene = expanded_session.state.get("expanded_scene", {})
                if isinstance(expanded_scene, str):
                    expanded_scene = json.loads(expanded_scene)
                
                expanded_scenes.append(expanded_scene)
            
            return expanded_scenes
        
        # Run Phase B
        expanded_scenes = asyncio.run(expand_all_scenes())
        logger.info(f"✅ Phase B complete: {len(expanded_scenes)} scenes expanded (English)")
        
        # Save English version first
        english_script = {
            "segment_id": outline_data.get("segment_id", f"{doc}_{reading}"),
            "duration_est_min": outline_data.get("duration_est_min", 40),
            "scenes": expanded_scenes
        }
        english_script_path = output_dir / "video_script_en.json"
        english_script_path.write_text(json.dumps(english_script, indent=2, ensure_ascii=False))
        logger.info(f"English script saved to {english_script_path}")
        
        # =====================================================
        # Phase C: DeepSeek Translation (English → Chinese)
        # =====================================================
        logger.info("🔄 Starting Phase C: DeepSeek translation to Chinese...")
        
        from google.genai import types as genai_types
        
        async def translate_script():
            """Phase C: Translate English script to Chinese using DeepSeek"""
            translate_session = await session_service.create_session(
                app_name="cfa_factory",
                user_id="cli_user",
                state={
                    "english_script": json.dumps(english_script, ensure_ascii=False),
                }
            )
            
            translate_runner = Runner(
                agent=script_translator,
                app_name="cfa_factory",
                session_service=session_service
            )
            
            async for event in translate_runner.run_async(
                user_id="cli_user",
                session_id=translate_session.id,
                new_message=genai_types.Content(parts=[genai_types.Part(text="Translate this English script to natural Chinese.")])
            ):
                pass
            
            final_session = await session_service.get_session(
                app_name="cfa_factory",
                user_id="cli_user",
                session_id=translate_session.id
            )
            
            translated = final_session.state.get("translated_script", {})
            if isinstance(translated, str):
                translated = json.loads(translated)
            return translated
        
        script_data = asyncio.run(translate_script())
        logger.info("✅ Phase C complete: Chinese translation done")
        
        # Save Chinese JSON version
        script_path = output_dir / "video_script.json"
        script_path.write_text(json.dumps(script_data, indent=2, ensure_ascii=False))
        logger.info(f"Chinese video script saved to {script_path}")
        
        # Save human-readable Chinese version
        scenes = script_data.get("scenes", [])
        if scenes:
            script_txt = output_dir / "script_zh.txt"
            total_chars = 0
            with script_txt.open("w", encoding="utf-8") as f:
                f.write(f"# 视频脚本: {script_data.get('segment_id', 'Unknown')}\n")
                f.write(f"# 预计时长: {script_data.get('duration_est_min', '?')} 分钟\n")
                f.write(f"# 场景数量: {len(scenes)}\n\n")
                for i, scene in enumerate(scenes, 1):
                    f.write(f"## Scene {i}: {scene.get('beat', 'Unknown')}\n")
                    f.write(f"【画面】{scene.get('display_zh', '')}\n")
                    speaker_en = scene.get('speaker', 'Narrator')
                    speaker_map = {"Professor": "教授", "Student": "学员", "Narrator": "旁白"}
                    speaker_label = speaker_map.get(speaker_en, speaker_en)
                    spoken = scene.get('spoken_zh', '')
                    total_chars += len(spoken)
                    f.write(f"【{speaker_label}】{spoken}\n")
                    if scene.get("citations"):
                        f.write(f"【引用】{', '.join(scene['citations'])}\n")
                    f.write("\n")
                f.write(f"\n# 总字数: {total_chars} 字\n")
            logger.info(f"Human-readable script saved to {script_txt}")
            logger.info(f"📊 Total characters: {total_chars} (~{total_chars // 150} minutes of speech)")
    
    # 9. Log summary
    if "verifier_report" in final_state and final_state["verifier_report"]:
        report = final_state["verifier_report"]
        if isinstance(report, str):
            report = json.loads(report)
        logger.info(f"Verifier Decision: {report.get('overall_decision', 'UNKNOWN')}")


def main():
    app()


if __name__ == "__main__":
    main()
