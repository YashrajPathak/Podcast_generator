# agent2.py – 📝 Production-Grade Summarizer & Analyst Agent (v2.2.2)

import os
import json
import asyncio
import logging
import time
import re
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from tenacity import retry, stop_after_attempt, wait_exponential

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import AzureChatOpenAI
from httpx import AsyncClient, Timeout, Response
from dotenv import load_dotenv

load_dotenv()

# ---- optional utils fallback ----
try:
    from utils import (
        clean_text_for_processing,
        validate_session_id,
        is_session_muted as utils_is_session_muted,
    )
except Exception:
    def clean_text_for_processing(text: str, max_length: int) -> str:
        if not isinstance(text, str):
            text = str(text) if text is not None else ""
        text = " ".join(text.split())
        if len(text) > max_length:
            text = text[:max_length] + "..."
        return text
    def validate_session_id(session_id: str) -> bool:
        return bool(session_id) and re.match(r"^[a-zA-Z0-9_-]+$", session_id) is not None
    async def utils_is_session_muted(session_id: str, agent: Optional[str] = None) -> bool:
        return False

# ========== Settings ==========
class Settings(BaseSettings):
    # LLM
    AZURE_OPENAI_KEY: str
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4o"
    OPENAI_API_VERSION: str = "2024-05-01-preview"

    # Orchestrator + TTS
    ORCHESTRATOR_URL: str = "http://localhost:8008"
    AGENT3_URL: str = "http://localhost:8004"  # Agent3 base (we’ll fetch WAVs from here)

    # Behavior
    AGENT_TIMEOUT: float = 30.0
    MAX_TEXT_LENGTH: int = 15000
    MAX_RETRIES: int = 3
    MAX_SEGMENTS: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()

# ========== Logging ==========
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Agent2")

def _orch_base() -> str:
    base = (settings.ORCHESTRATOR_URL or "http://localhost:8008").rstrip("/")
    if base.endswith("/conversation"):
        logger.warning("[Agent2] ORCHESTRATOR_URL had /conversation; stripping")
        base = base[:-len("/conversation")]
    return base

def _agent3_base() -> str:
    return (settings.AGENT3_URL or "http://localhost:8004").rstrip("/")

# ---------- Audio extraction helper (prefer inline hex, then local, then HTTP) ----------
async def _extract_audio_from_agent3_response(
    data: Dict[str, Any],
    agent3_base: str,
    timeout: float
) -> Tuple[Optional[str], bytes]:
    """Return (audio_path, audio_bytes)."""
    if not isinstance(data, dict):
        return None, b""

    hex_data = data.get("audio_hex")
    audio_path = data.get("audio_path")

    # 1) Prefer inline bytes (most reliable)
    if isinstance(hex_data, str) and hex_data:
        try:
            return audio_path, bytes.fromhex(hex_data)
        except Exception as e:
            logger.warning(f"[Agent2][TTS] hex decode failed: {e}")

    # 2) Try local path on shared volume (if any)
    if isinstance(audio_path, str) and audio_path:
        try:
            p = Path(audio_path)
            if p.exists():
                return audio_path, p.read_bytes()
        except Exception as e:
            logger.debug(f"[Agent2][TTS] local read miss ({audio_path}): {e}")

        # 3) Fallback to HTTP fetch (absolute or relative to Agent3)
        try:
            if audio_path.startswith(("http://", "https://")):
                fetch_url = audio_path
            else:
                # if Agent3 returned "audio_cache/xyz.wav" or "/audio/xyz.wav"
                fetch_url = f"{agent3_base}/{audio_path.lstrip('/')}"
            async with AsyncClient(timeout=Timeout(timeout)) as client:
                r2 = await client.get(fetch_url)
            if r2.status_code == 200 and r2.content:
                return audio_path, r2.content
            logger.warning(f"[Agent2][TTS] HTTP fetch failed {fetch_url} -> {r2.status_code}")
        except Exception as e:
            logger.warning(f"[Agent2][TTS] HTTP fetch exception: {e}")

    return None, b""

# ========== Models ==========
class SummarizeRequest(BaseModel):
    raw_script: str = Field(..., min_length=1, max_length=50000)
    session_id: str = Field(default="default")
    max_segments: int = Field(default=settings.MAX_SEGMENTS, ge=1, le=50)
    include_topics: bool = Field(default=True)
    include_bullet_points: bool = Field(default=True)

class SummarizeResponse(BaseModel):
    summary: str
    segments: List[Dict[str, Any]]
    topics: List[str]
    bullet_points: List[str]
    session_id: str
    status: str = "success"
    processing_time: Optional[float] = None
    agent_info: Optional[Dict[str, Any]] = None

class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    session_id: str = Field(default="default")
    voice: str = Field(default="en-GB-RyanNeural")
    conversation_mode: str = Field(default="agent_to_agent")
    is_conversation_turn: bool = Field(default=False)
    is_interruption: bool = Field(default=False)
    turn_number: Optional[int] = None
    max_turns: Optional[int] = None
    conversation_context: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    audio_path: Optional[str] = None     # set to Agent3 URL or path for reference
    audio_hex: Optional[str] = None      # inline bytes for Graph playback
    session_id: str
    status: str = "success"
    processing_time: Optional[float] = None
    agent_info: Optional[Dict[str, Any]] = None
    turn_number: Optional[int] = None

class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=1000)
    session_id: str = Field(default="default")

# ========== Robust JSON Parser ==========
class RobustJSONParser:
    @staticmethod
    def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        best_obj = None
        stack, start_idx = [], None
        for i, ch in enumerate(text):
            if ch == "{":
                if not stack: start_idx = i
                stack.append("{")
            elif ch == "}" and stack:
                stack.pop()
                if not stack and start_idx is not None:
                    block = text[start_idx:i+1]
                    try:
                        best_obj = json.loads(block)
                    except Exception:
                        pass
        if best_obj is not None:
            return best_obj
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                return {"segments": arr}
        except Exception:
            pass
        l_start, l_end = text.find("["), text.rfind("]")
        if l_start != -1 and l_end != -1 and l_end > l_start:
            try:
                arr = json.loads(text[l_start:l_end+1])
                if isinstance(arr, list):
                    return {"segments": arr}
            except Exception:
                pass
        return None

    @staticmethod
    def create_fallback_summary(text: str, max_segments: int = 5) -> Dict[str, Any]:
        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        segments = [{
            "id": i + 1,
            "content": sentence,
            "type": "summary_point",
            "timestamp": f"{i * 30}s",
        } for i, sentence in enumerate(sentences[:max_segments])]
        return {
            "summary": "Generated summary from content analysis",
            "segments": segments,
            "topics": ["content_analysis", "auto_generated"],
            "bullet_points": sentences[:max_segments],
        }

# ========== Mute helper ==========
@retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _fetch_mute_status_fallback(session_id: str) -> Optional[bool]:
    url = f"{_orch_base()}/session/{session_id}/mute-status"
    params = {"agent": "agent2"}
    async with AsyncClient(timeout=Timeout(settings.AGENT_TIMEOUT)) as client:
        r = await client.get(url, params=params)
        if r.status_code >= 500:
            raise RuntimeError(f"transient {r.status_code}")
        if r.status_code != 200:
            return None
        return bool(r.json().get("muted", False))

async def is_effectively_muted(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    try:
        return bool(await utils_is_session_muted(session_id, "agent2"))
    except Exception:
        pass
    try:
        res = await _fetch_mute_status_fallback(session_id)
        return bool(res) if res is not None else False
    except Exception:
        return False

# ========== Agent ==========
class Agent2:
    def __init__(self):
        self._start_time = time.time()
        self.llm = AzureChatOpenAI(
            deployment_name=settings.AZURE_OPENAI_DEPLOYMENT,
            api_key=settings.AZURE_OPENAI_KEY,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.OPENAI_API_VERSION,
            temperature=0.4,
            timeout=settings.AGENT_TIMEOUT,
        )
        self.json_parser = RobustJSONParser()
        logger.info("✅ Agent2 initialized (LLM ready)")

    # ---------- TTS via Agent3 (returns URL/path + bytes, preferring audio_hex) ----------
    @retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def tts_via_agent3(self, *, text: str, voice: str, session_id: str) -> Tuple[Optional[str], bytes]:
        if not text.strip():
            return None, b""
        gen_url = f"{_agent3_base()}/generate-audio"
        payload = {"text": text, "voice": voice, "speed": 1.0, "pitch": 1.0, "session_id": session_id}

        async with AsyncClient(timeout=Timeout(settings.AGENT_TIMEOUT)) as client:
            r = await client.post(gen_url, json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"Agent3 TTS failed ({r.status_code}): {r.text}")

        data = r.json()
        audio_path, audio_bytes = await _extract_audio_from_agent3_response(
            data=data,
            agent3_base=_agent3_base(),
            timeout=settings.AGENT_TIMEOUT
        )
        if not audio_bytes:
            logger.warning("[Agent2][TTS] No audio bytes returned after extraction (will return text-only).")
        return (data.get("audio_path") or audio_path), audio_bytes

    # --------- Summarize ---------
    @retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def summarize(self, request: SummarizeRequest) -> SummarizeResponse:
        start = time.time()
        try:
            if not validate_session_id(request.session_id):
                raise ValueError("Invalid session_id")
            if await is_effectively_muted(request.session_id):
                return SummarizeResponse(summary="", segments=[], topics=[], bullet_points=[],
                                         session_id=request.session_id, status="muted", processing_time=0.0,
                                         agent_info={"agent": "agent2", "muted": True})

            cleaned_text = clean_text_for_processing(request.raw_script, settings.MAX_TEXT_LENGTH)
            if not cleaned_text:
                raise ValueError("Empty or invalid input text")

            max_segments = min(max(1, request.max_segments), settings.MAX_SEGMENTS)
            system_prompt = (
                "You are an expert content analyst. Return ONLY valid JSON with the schema: "
                '{"summary": str, "segments": [{"id": int, "content": str, "type": "main_point|sub_point|example|conclusion", "timestamp": "mm:ss"}], '
                '"topics": [str], "bullet_points": [str]}. '
                f"Constraints: Max segments={max_segments}, include_topics={request.include_topics}, include_bullet_points={request.include_bullet_points}. "
                "Use mm:ss timestamps and do not include any text outside JSON."
            )
            user_prompt = f"Content to analyze:\n{cleaned_text}"
            resp = await self.llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
            response_text = (resp.content or "").strip()
            if not response_text:
                raise ValueError("LLM returned empty response")

            parsed = self.json_parser.extract_json_from_text(response_text)
            used_fallback = False
            if not parsed:
                parsed = self.json_parser.create_fallback_summary(cleaned_text, max_segments=max_segments)
                used_fallback = True

            structured = self._validate_and_structure_summary(parsed, max_segments)
            elapsed = time.time() - start
            return SummarizeResponse(
                summary=structured["summary"], segments=structured["segments"],
                topics=structured["topics"], bullet_points=structured["bullet_points"],
                session_id=request.session_id, status="success", processing_time=elapsed,
                agent_info={"agent": "agent2", "parsing_method": "fallback" if used_fallback else "json"}
            )
        except Exception as e:
            elapsed = time.time() - start
            return SummarizeResponse(summary="Error occurred during summarization", segments=[], topics=[],
                                     bullet_points=[], session_id=request.session_id, status="error",
                                     processing_time=elapsed, agent_info={"error": str(e)})

    # --------- Chat ---------
    @retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        start = time.time()
        try:
            if not validate_session_id(request.session_id):
                raise ValueError("Invalid session_id")
            if await is_effectively_muted(request.session_id):
                return ChatResponse(response="", audio_path=None, audio_hex=None,
                                    session_id=request.session_id, status="muted",
                                    processing_time=0.0, agent_info={"agent": "agent2", "muted": True},
                                    turn_number=request.turn_number)

            system_prompt = (
                "You are Agent2: an analytical expert and researcher in a podcast panel. "
                "Provide data-driven, concise (2–3 sentences) insights with a conversational tone."
            )
            if request.is_interruption:
                system_prompt += " This is a response to a user interruption; address it directly and succinctly."

            user_message = self._build_chat_user_message(request)
            resp = await self.llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_message)])
            response_text = (resp.content or "").strip()
            if not response_text:
                raise ValueError("LLM returned empty response")

            audio_url: Optional[str] = None
            audio_hex: Optional[str] = None
            try:
                audio_url, audio_bytes = await self.tts_via_agent3(
                    text=response_text,
                    voice=request.voice,
                    session_id=request.session_id
                )
                if audio_bytes:
                    audio_hex = audio_bytes.hex()
            except Exception as e:
                logger.warning(f"[Agent2] TTS step failed: {e}")

            elapsed = time.time() - start
            return ChatResponse(
                response=response_text,
                audio_path=audio_url,  # Graph prefers audio_hex; path kept for reference/debug
                audio_hex=audio_hex,
                session_id=request.session_id,
                status="success",
                processing_time=elapsed,
                agent_info={
                    "agent": "agent2",
                    "conversation_mode": request.conversation_mode,
                    "is_interruption": request.is_interruption,
                    "voice": request.voice,
                    "audio_generated": bool(audio_hex),
                },
                turn_number=request.turn_number,
            )
        except Exception as e:
            elapsed = time.time() - start
            return ChatResponse(response=f"Error: {e}", audio_path=None, audio_hex=None,
                                session_id=request.session_id, status="error",
                                processing_time=elapsed, turn_number=request.turn_number)

    # --------- Command ---------
    async def handle_command(self, request: CommandRequest) -> Dict[str, Any]:
        start = time.time()
        try:
            if not validate_session_id(request.session_id):
                return {"error": "Invalid session_id", "session_id": request.session_id}
            cmd = (request.command or "").strip()
            low = cmd.lower()

            if low.startswith("/analyze"):
                text = cmd[len("/analyze"):].strip()
                if not text:
                    return {"error": "No text provided for analysis", "session_id": request.session_id}
                return (await self.summarize(SummarizeRequest(raw_script=text, session_id=request.session_id, max_segments=5))).dict()

            if low.startswith("/topics"):
                text = cmd[len("/topics"):].strip()
                if not text:
                    return {"error": "No text provided for topic extraction", "session_id": request.session_id}
                topics = await self._extract_topics(text)
                return {"topics": topics, "session_id": request.session_id, "status": "success"}

            if low.startswith("/segments"):
                text = cmd[len("/segments"):].strip()
                if not text:
                    return {"error": "No text provided for segmentation", "session_id": request.session_id}
                segments = await self._create_segments(text)
                return {"segments": segments, "session_id": request.session_id, "status": "success"}

            return {
                "error": "Unknown command",
                "available_commands": ["/analyze", "/topics", "/segments"],
                "usage": "Use /analyze <text>, /topics <text>, or /segments <text>",
                "session_id": request.session_id,
            }
        except Exception as e:
            return {"error": f"line-of-command failed: {str(e)}", "session_id": request.session_id,
                    "processing_time": time.time() - start}

    # --------- Helpers ---------
    def _build_chat_user_message(self, req: ChatRequest) -> str:
        parts = [f"User message:\n{req.text}"]
        if req.turn_number and req.max_turns:
            parts.append(f"Conversation turn: {req.turn_number} of {req.max_turns}.")
        if req.conversation_context:
            parts.append(f"Recent conversation context:\n{req.conversation_context}")
        return "\n\n".join(parts)

    def _validate_and_structure_summary(self, parsed: Dict[str, Any], max_segments: int) -> Dict[str, Any]:
        summary = parsed.get("summary", "Generated summary")
        segments = parsed.get("segments", [])
        topics = parsed.get("topics", [])
        bullet_points = parsed.get("bullet_points", [])

        cleaned_segments: List[Dict[str, Any]] = []
        if isinstance(segments, list):
            for i, seg in enumerate(segments[:max_segments]):
                if isinstance(seg, dict):
                    cleaned_segments.append({
                        "id": seg.get("id", i + 1),
                        "content": str(seg.get("content", "")),
                        "type": seg.get("type", "main_point"),
                        "timestamp": seg.get("timestamp", f"{i * 30}s"),
                    })

        if not isinstance(topics, list):
            topics = [str(topics)] if topics else []
        if not isinstance(bullet_points, list):
            bullet_points = [str(bullet_points)] if bullet_points else []

        topics = [str(t) for t in topics[:10]]
        bullet_points = [str(b) for b in bullet_points[:10]]

        return {
            "summary": str(summary),
            "segments": cleaned_segments,
            "topics": topics,
            "bullet_points": bullet_points,
        }

    async def _extract_topics(self, text: str) -> List[str]:
        try:
            prompt = f"""Extract 5–8 main topics from the following text. RETURN ONLY a JSON array of strings.

Text:
{text}

Respond with: ["topic1","topic2","topic3"]"""
            resp = await self.llm.ainvoke([SystemMessage(content="Return only a JSON array, no prose."), HumanMessage(content=prompt)])
            try:
                parsed = json.loads((resp.content or "").strip())
                if isinstance(parsed, list):
                    return [str(t) for t in parsed[:8]]
            except Exception:
                pass
            alt = RobustJSONParser.extract_json_from_text((resp.content or "").strip())
            if isinstance(alt, list):
                return [str(t) for t in alt[:8]]
            return []
        except Exception:
            return []

    async def _create_segments(self, text: str) -> List[Dict[str, Any]]:
        try:
            prompt = f"""Break the text into 5–8 logical segments. RETURN ONLY a JSON array of segment objects.

Each segment object: {{"id":1,"content":"...","type":"main_point","timestamp":"00:30"}}

Text:
{text}"""
            resp = await self.llm.ainvoke([SystemMessage(content="Return only a JSON array, no prose."), HumanMessage(content=prompt)])
            try:
                parsed = json.loads((resp.content or "").strip())
            except Exception:
                parsed = RobustJSONParser.extract_json_from_text((resp.content or "").strip())
            if isinstance(parsed, list):
                out: List[Dict[str, Any]] = []
                for i, seg in enumerate(parsed[:8]):
                    if isinstance(seg, dict):
                        out.append({
                            "id": seg.get("id", i + 1),
                            "content": str(seg.get("content", "")),
                            "type": seg.get("type", "main_point"),
                            "timestamp": seg.get("timestamp", f"{i * 30}s"),
                        })
                return out
            return []
        except Exception:
            return []

# ========== FastAPI ==========
app = FastAPI(
    title="📝 Agent2 - Summarizer & Analyst",
    description="Production-grade summarizer and analyst agent for podcast content",
    version="2.2.2",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

agent = Agent2()

@app.on_event("startup")
async def startup():
    required = ["AZURE_OPENAI_KEY","AZURE_OPENAI_ENDPOINT","AZURE_OPENAI_DEPLOYMENT","OPENAI_API_VERSION","ORCHESTRATOR_URL","AGENT3_URL"]
    presence = {k: ("present" if os.getenv(k) else "MISSING") for k in required}
    print(f"[Agent2][STARTUP] Env presence: {presence}")
    print(f"[Agent2][STARTUP] ORCHESTRATOR_BASE={_orch_base()}")
    print(f"[Agent2][STARTUP] Agent3 base={_agent3_base()}")

# Endpoints
@app.post("/v1/summarize", response_model=SummarizeResponse)
async def summarize_handler(request: SummarizeRequest):
    if not validate_session_id(request.session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    return await agent.summarize(request)

@app.post("/v1/chat", response_model=ChatResponse)
async def chat_handler(request: ChatRequest):
    if not validate_session_id(request.session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    return await agent.handle_chat(request)

@app.post("/v1/command")
async def command_handler(request: CommandRequest):
    if not validate_session_id(request.session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    return await agent.handle_command(request)

@app.get("/health")
async def health():
    try:
        req = [bool(settings.AZURE_OPENAI_KEY), bool(settings.AZURE_OPENAI_ENDPOINT), bool(settings.AZURE_OPENAI_DEPLOYMENT)]
        return {"status": "healthy" if all(req) else "degraded", "service": "Agent2 - Summarizer & Analyst",
                "version": "2.2.2", "uptime": time.time() - agent._start_time}
    except Exception as e:
        logger.error(f"[Agent2][HEALTH] {e}")
        return {"status": "unhealthy", "error": str(e)}

@app.get("/")
async def root():
    return {
        "service": "Agent2 - Summarizer & Analyst",
        "version": "2.2.2",
        "endpoints": {"summarize": "/v1/summarize", "chat": "/v1/chat", "command": "/v1/command", "health": "/health", "debug/config": "/debug/config"},
        "features": [
            "JSON summarization with fallback",
            "Concise analytical replies",
            "Per-session mute integration",
            "🔊 TTS via Agent3 with audio_hex passthrough + robust fallback",
        ],
    }

# ---- Debug config (safe) ----
from typing import List as _List
import os as _os
def _present(k: str) -> bool: v = _os.getenv(k, ""); return bool(v and v.strip())
def _looks_url(k: str) -> bool: v = _os.getenv(k, ""); return v.startswith("http://") or v.startswith("https://")
def attach_debug_config(app, service_name: str, required_vars: _List[str], url_vars: _List[str] = []):
    @app.get("/debug/config")
    def debug_config():
        ok = {}
        for k in required_vars: ok[k] = "present" if _present(k) else "MISSING"
        for k in url_vars:
            if _present(k): ok[k] = ok.get(k, "present")
            ok[f"{k}_is_url"] = "ok" if _looks_url(k) else "INVALID_URL"
        return {"service": service_name, "vars": ok}
attach_debug_config(app, "agent2",
    required_vars=["AZURE_OPENAI_KEY","AZURE_OPENAI_ENDPOINT","AZURE_OPENAI_DEPLOYMENT","OPENAI_API_VERSION","ORCHESTRATOR_URL","AGENT3_URL"],
    url_vars=["ORCHESTRATOR_URL","AGENT3_URL"]
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")
