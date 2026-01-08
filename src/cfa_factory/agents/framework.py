# =============================================================================
# CFA Factory Agent Framework (Google ADK Native)
# =============================================================================
# This module re-exports Google ADK primitives and provides custom wrappers
# for third-party models (e.g., DeepSeek).
# =============================================================================

from __future__ import annotations

import os
import json
import logging
from typing import Dict, Any, Optional, Type

from dotenv import load_dotenv
from pydantic import BaseModel
import openai

# --- Google ADK Native Imports ---
from google.adk.agents import LlmAgent, SequentialAgent, ParallelAgent, LoopAgent
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.adk.tools import google_search  # Built-in web search tool

load_dotenv()
logger = logging.getLogger(__name__)

# =============================================================================
# DeepSeek Wrapper (for OpenAI-compatible third-party models)
# =============================================================================
# Google ADK's LlmAgent expects a "model" string for Gemini models.
# For third-party models like DeepSeek, we need a custom callable wrapper.
# However, ADK's LlmAgent may not directly support arbitrary model wrappers.
#
# Approach: We'll create a custom agent class that extends BaseAgent and
# uses the OpenAI client internally for DeepSeek calls.
# =============================================================================

from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.genai import types as genai_types


class DeepSeekAgent(BaseAgent):
    """
    A custom agent that uses DeepSeek API (OpenAI-compatible) instead of Gemini.
    Works as a drop-in replacement for LlmAgent in Sequential workflows.
    """
    # Pydantic fields (class-level annotations)
    instruction: str = ""
    output_schema: Optional[Type[BaseModel]] = None
    output_key: Optional[str] = None
    deepseek_model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    max_retries: int = 0
    retry_hint: str = "Your last response was invalid JSON. Return ONLY valid JSON without extra text."
    retry_include_last_output: bool = True
    retry_max_chars: int = 4000
    raw_output_key: Optional[str] = None
    save_raw_always: bool = False
    
    # Non-pydantic fields (excluded from serialization)
    _client: Any = None
    
    def model_post_init(self, __context: Any) -> None:
        """Initialize OpenAI client after Pydantic validation."""
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            logger.warning(f"Missing API key: {self.api_key_env} - DeepSeekAgent may fail at runtime")
        else:
            self._client = openai.Client(api_key=api_key, base_url=self.base_url)

    async def _run_async_impl(self, ctx):
        """
        Execute the DeepSeek agent logic.
        ADK calls this method during workflow execution.
        """
        if not self._client:
            api_key = os.getenv(self.api_key_env)
            if not api_key:
                raise ValueError(f"Missing API key: {self.api_key_env}")
            self._client = openai.Client(api_key=api_key, base_url=self.base_url)
        
        # Render instruction with state values (simple string replacement)
        rendered_instruction = self.instruction
        for key, value in ctx.session.state.items():
            placeholder = "{" + key + "}"
            if placeholder in rendered_instruction:
                if isinstance(value, (dict, list)):
                    rendered_instruction = rendered_instruction.replace(placeholder, json.dumps(value, ensure_ascii=False))
                else:
                    rendered_instruction = rendered_instruction.replace(placeholder, str(value))
        
        # Call DeepSeek API with optional retries for JSON validity
        base_messages = [{"role": "user", "content": rendered_instruction}]
        response_format = {"type": "json_object"} if self.output_schema else None

        last_error: Exception | None = None
        last_content: str | None = None
        for attempt in range(self.max_retries + 1):
            messages = list(base_messages)
            if attempt > 0:
                if self.retry_include_last_output and last_content:
                    snippet = last_content[: self.retry_max_chars]
                    messages.append({"role": "assistant", "content": snippet})
                    messages.append({
                        "role": "user",
                        "content": f"{self.retry_hint}\nError: {last_error}\nFix the JSON above and output ONLY JSON."
                    })
                else:
                    messages.append({"role": "user", "content": self.retry_hint})
                logger.warning(
                    f"DeepSeek agent [{self.name}] retry {attempt}/{self.max_retries}"
                )
            try:
                response = self._client.chat.completions.create(
                    model=self.deepseek_model,
                    messages=messages,
                    response_format=response_format,
                    stream=False
                )
                content = response.choices[0].message.content
                last_content = content

                if self.raw_output_key and (self.save_raw_always or self.output_schema is None):
                    ctx.session.state[self.raw_output_key] = content

                # Parse and validate if schema is provided
                if self.output_schema:
                    # Sanitize markdown code blocks if present
                    content = content.replace("```json", "").replace("```", "").strip()
                    try:
                        parsed = self.output_schema.model_validate_json(content)
                        result = parsed.model_dump()
                    except Exception as e:
                        last_content = content
                        raise e
                else:
                    result = content

                # Store in session state
                if self.output_key:
                    ctx.session.state[self.output_key] = result

                # Yield event (ADK expects async generator)
                yield Event(
                    author=self.name,
                    content=genai_types.Content(
                        parts=[genai_types.Part(text=json.dumps(result) if isinstance(result, dict) else result)]
                    )
                )
                return
            except Exception as e:
                last_error = e
                logger.warning(f"DeepSeek agent [{self.name}] attempt {attempt} failed: {e}")
                if attempt >= self.max_retries:
                    break

        if self.raw_output_key and last_content:
            ctx.session.state[self.raw_output_key] = last_content
        logger.error(f"DeepSeek agent [{self.name}] failed after retries: {last_error}")
        raise last_error


# =============================================================================
# Per-Scene Translator (DeepSeek, non-JSON output)
# =============================================================================
class PerSceneTranslatorAgent(BaseAgent):
    """
    Translate per-scene to avoid long JSON truncation.
    Outputs VideoScriptSchema dict to output_key.
    """
    instruction: str = ""
    output_schema: Optional[Type[BaseModel]] = None
    output_key: Optional[str] = None
    input_key: str = "english_script"
    raw_output_key: Optional[str] = None
    deepseek_model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    max_retries: int = 3
    smooth_zh: bool = True
    smooth_window: int = 1
    smooth_ratio_min: float = 0.7
    smooth_ratio_max: float = 1.3

    _client: Any = None

    def model_post_init(self, __context: Any) -> None:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            logger.warning(f"Missing API key: {self.api_key_env} - PerSceneTranslatorAgent may fail at runtime")
        else:
            self._client = openai.Client(api_key=api_key, base_url=self.base_url)

    async def _run_async_impl(self, ctx):
        if not self._client:
            api_key = os.getenv(self.api_key_env)
            if not api_key:
                raise ValueError(f"Missing API key: {self.api_key_env}")
            self._client = openai.Client(api_key=api_key, base_url=self.base_url)

        raw_script = ctx.session.state.get(self.input_key)
        if raw_script is None:
            raise ValueError(f"Missing {self.input_key} in session state.")

        if isinstance(raw_script, str):
            try:
                english_script = json.loads(raw_script)
            except Exception:
                english_script = {"raw": raw_script}
        else:
            english_script = raw_script

        scenes = english_script.get("scenes", [])
        if not scenes:
            raise ValueError("English script missing scenes.")

        translated_scenes = []
        raw_outputs = [None] * len(scenes)

        smooth_enabled = ctx.session.state.get("smooth_zh", self.smooth_zh)
        if isinstance(smooth_enabled, str):
            smooth_enabled = smooth_enabled.strip().lower() in ("1", "true", "yes", "y")

        smooth_window = ctx.session.state.get("smooth_window", self.smooth_window)
        try:
            smooth_window = max(0, int(smooth_window))
        except Exception:
            smooth_window = self.smooth_window

        for idx, scene in enumerate(scenes):
            display_en = scene.get("display_en", "")
            spoken_en = scene.get("spoken_en", "")
            speaker = scene.get("speaker", "Narrator")
            prev_spoken = scenes[idx - 1].get("spoken_en", "") if idx > 0 else ""
            next_spoken = scenes[idx + 1].get("spoken_en", "") if idx + 1 < len(scenes) else ""

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
                f"SPEAKER: {speaker}\n"
                f"DISPLAY_EN: {display_en}\n"
                f"SPOKEN_EN: {spoken_en}\n"
            )

            last_output = ""
            display_zh = ""
            spoken_zh = ""

            for attempt in range(self.max_retries + 1):
                resp = self._client.chat.completions.create(
                    model=self.deepseek_model,
                    messages=[{"role": "user", "content": base_prompt}],
                    stream=False
                )
                content = resp.choices[0].message.content.strip()
                last_output = content

                lines = [l.strip() for l in content.splitlines() if l.strip()]
                display_zh = ""
                spoken_zh = ""
                for line in lines:
                    if line.startswith("DISPLAY_ZH:"):
                        display_zh = line[len("DISPLAY_ZH:"):].strip()
                    elif line.startswith("SPOKEN_ZH:"):
                        spoken_zh = line[len("SPOKEN_ZH:"):].strip()

                if display_zh and spoken_zh:
                    break

            if not display_zh or not spoken_zh:
                raise ValueError(f"Per-scene translation failed at scene {idx + 1}. Last output: {last_output}")

            raw_outputs[idx] = {"scene": idx + 1, "translate_raw": last_output, "smooth_raw": None}
            translated_scenes.append({
                "beat": scene.get("beat"),
                "speaker": scene.get("speaker", "Narrator"),
                "display_zh": display_zh,
                "spoken_zh": spoken_zh,
                "citations": scene.get("citations", []),
                "visual_refs": scene.get("visual_refs", []),
                "quiz": scene.get("quiz")
            })

        if smooth_enabled and translated_scenes:
            context_spoken = [s.get("spoken_zh", "") for s in translated_scenes]
            for idx, scene in enumerate(translated_scenes):
                original = scene.get("spoken_zh", "")
                if not original:
                    continue
                prev_ctx = "\n".join(
                    s for s in context_spoken[max(0, idx - smooth_window):idx] if s
                )
                next_ctx = "\n".join(
                    s for s in context_spoken[idx + 1:idx + 1 + smooth_window] if s
                )
                speaker = scene.get("speaker", "Narrator")
                smooth_prompt = (
                    "You are a Chinese dialogue editor. Polish ONLY the current SPOKEN_ZH.\n"
                    "Do NOT add or remove facts. Keep meaning intact.\n"
                    "Use context for smoother transitions, but do not introduce new information.\n"
                    "Keep length roughly similar (±20%). Preserve key terms/abbreviations.\n"
                    "Return exactly one line:\n"
                    "SPOKEN_ZH: ...\n"
                    "No extra text.\n\n"
                    f"SPEAKER: {speaker}\n"
                    f"PREV_CONTEXT: {prev_ctx}\n"
                    f"NEXT_CONTEXT: {next_ctx}\n"
                    f"CURRENT_SPOKEN_ZH: {original}\n"
                )

                last_output = ""
                smoothed = ""
                for attempt in range(self.max_retries + 1):
                    resp = self._client.chat.completions.create(
                        model=self.deepseek_model,
                        messages=[{"role": "user", "content": smooth_prompt}],
                        stream=False
                    )
                    content = resp.choices[0].message.content.strip()
                    last_output = content
                    for line in content.splitlines():
                        line = line.strip()
                        if line.startswith("SPOKEN_ZH:"):
                            smoothed = line[len("SPOKEN_ZH:"):].strip()
                            break
                    if smoothed:
                        ratio = len(smoothed) / max(1, len(original))
                        if self.smooth_ratio_min <= ratio <= self.smooth_ratio_max:
                            break
                        smoothed = ""
                if smoothed:
                    scene["spoken_zh"] = smoothed
                    if raw_outputs[idx] is None:
                        raw_outputs[idx] = {"scene": idx + 1, "translate_raw": None, "smooth_raw": last_output}
                    else:
                        raw_outputs[idx]["smooth_raw"] = last_output

        result = {
            "segment_id": english_script.get("segment_id", "segment"),
            "duration_est_min": english_script.get("duration_est_min"),
            "scenes": translated_scenes
        }

        if self.raw_output_key:
            ctx.session.state[self.raw_output_key] = raw_outputs
        if self.output_key:
            ctx.session.state[self.output_key] = result

        yield Event(
            author=self.name,
            content=genai_types.Content(
                parts=[genai_types.Part(text=json.dumps(result, ensure_ascii=False))]
            )
        )

# =============================================================================
# Convenience Exports
# =============================================================================
# Re-export ADK primitives for easy imports in core.py

__all__ = [
    "LlmAgent",
    "SequentialAgent",
    "ParallelAgent",
    "LoopAgent",
    "DeepSeekAgent",
    "PerSceneTranslatorAgent",
    "InMemorySessionService",
    "Runner",
    "google_search",
]
