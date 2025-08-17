# agent2.py – 📝 Production-Grade Summarizer & Analyst Agent (v2.1.4)

import os
import json
import asyncio
import logging
import time
import re
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from tenacity import retry, stop_after_attempt, wait_exponential

from langchain_core.messages import SystemMessage, HumanMessage
# ✅ non-deprecated client
from langchain_openai import AzureChatOpenAI
from httpx import AsyncClient, Timeout
from dotenv import load_dotenv

# ---------- Load .env early ----------
load_dotenv()

# If you have these utils in your repo, import; else fall back to locals.
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
        return False  # noop fallback if utils isn't available


# ========== Settings ==========
class Settings(BaseSettings):
    AZURE_OPENAI_KEY: str
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4o"
    OPENAI_API_VERSION: str = "2024-05-01-preview"

    AGENT_TIMEOUT: float = 30.0
    MAX_TEXT_LENGTH: int = 15000
    MAX_RETRIES: int = 3
    MAX_SEGMENTS: int = 10  # hard ceiling
    ORCHESTRATOR_URL: str = "http://localhost:8008"  # Graph base (no /conversation)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


try:
    settings = Settings()
except Exception as e:
    print(f"❌ Agent2 Configuration Error: {e}")
    raise


# ========== Logging ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("Agent2")


# ---------- Helpers ----------
def _orch_base() -> str:
    """Return normalized orchestrator base URL (no trailing slash or /conversation)."""
    base = (settings.ORCHESTRATOR_URL or "http://localhost:8008").rstrip("/")
    if base.endswith("/conversation"):
        logger.warning("[Agent2] ORCHESTRATOR_URL had trailing /conversation; stripping for API calls")
        base = base[:-len("/conversation")]
    return base


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
    audio_path: Optional[str] = None
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
    """Stack-based JSON extractor with sane fallbacks."""

    @staticmethod
    def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
        # direct parse
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        # largest balanced {...}
        best_obj = None
        stack = []
        start_idx = None
        for i, ch in enumerate(text):
            if ch == "{":
                if not stack:
                    start_idx = i
                stack.append("{")
            elif ch == "}" and stack:
                stack.pop()
                if not stack and start_idx is not None:
                    block = text[start_idx: i + 1]
                    try:
                        best_obj = json.loads(block)
                    except Exception:
                        pass
        if best_obj is not None:
            return best_obj

        # whole text as list
        try:
            arr = json.loads(text)
            if isinstance(arr, list):
                return {"segments": arr}
        except Exception:
            pass

        # first [...] region
        l_start = text.find("[")
        l_end = text.rfind("]")
        if l_start != -1 and l_end != -1 and l_end > l_start:
            try:
                arr = json.loads(text[l_start: l_end + 1])
                if isinstance(arr, list):
                    return {"segments": arr}
            except Exception:
                pass
        return None

    @staticmethod
    def create_fallback_summary(text: str, max_segments: int = 5) -> Dict[str, Any]:
        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        segments = []
        for i, sentence in enumerate(sentences[:max_segments]):
            segments.append(
                {
                    "id": i + 1,
                    "content": sentence,
                    "type": "summary_point",
                    "timestamp": f"{i * 30}s",
                }
            )
        return {
            "summary": "Generated summary from content analysis",
            "segments": segments,
            "topics": ["content_analysis", "auto_generated"],
            "bullet_points": sentences[:max_segments],
        }


# ========== Agent ==========
@retry(stop=stop_after_attempt(settings.MAX_RETRIES),
       wait=wait_exponential(multiplier=1, min=1, max=8))
async def _fetch_mute_status_fallback(session_id: str) -> Optional[bool]:
    """Fallback HTTP fetch to Graph mute-status with retry on transient errors."""
    url = f"{_orch_base()}/session/{session_id}/mute-status"
    params = {"agent": "agent2"}
    logger.debug(f"[Agent2][MUTE→Graph] GET {url} params={params}")
    async with AsyncClient(timeout=Timeout(settings.AGENT_TIMEOUT)) as client:
        r = await client.get(url, params=params)
        logger.debug(f"[Agent2][MUTE→Graph] status={r.status_code}")
        # Retry on 5xx
        if r.status_code >= 500:
            raise RuntimeError(f"transient {r.status_code}")
        if r.status_code != 200:
            return None
        data = r.json()
        return bool(data.get("muted", False))


async def is_effectively_muted(session_id: Optional[str]) -> bool:
    """Check Graph /mute-status with agent=agent2 via utils; fallback to direct HTTP with retry."""
    if not session_id:
        return False
    try:
        muted = bool(await utils_is_session_muted(session_id, "agent2"))
        logger.debug(f"[Agent2][MUTE] utils -> {muted}")
        return muted
    except Exception as e:
        logger.debug(f"[Agent2][MUTE] utils check failed: {e}")
    try:
        res = await _fetch_mute_status_fallback(session_id)
        muted2 = bool(res) if res is not None else False
        logger.debug(f"[Agent2][MUTE] fallback -> {muted2}")
        return muted2
    except Exception as e:
        logger.warning(f"[Agent2][MUTE] fallback failed: {e}")
        return False


class Agent2:
    """Summarizer & Analyst."""
    def __init__(self):
        self._start_time = time.time()
        try:
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
        except Exception as e:
            logger.critical(f"❌ LLM initialization failed: {e}")
            raise

    # --------- Summarize ---------
    @retry(stop=stop_after_attempt(settings.MAX_RETRIES),
           wait=wait_exponential(multiplier=1, min=1, max=8))
    async def summarize(self, request: SummarizeRequest) -> SummarizeResponse:
        start = time.time()
        try:
            if not validate_session_id(request.session_id):
                raise ValueError("Invalid session_id")

            if await is_effectively_muted(request.session_id):
                logger.info(f"🔇 Session {request.session_id} muted (agent2); skipping summarize")
                return SummarizeResponse(
                    summary="",
                    segments=[],
                    topics=[],
                    bullet_points=[],
                    session_id=request.session_id,
                    status="muted",
                    processing_time=0.0,
                    agent_info={"agent": "agent2", "muted": True},
                )

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

            messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            logger.debug(f"[Agent2][LLM] summarize ainvoke messages={len(messages)} input_len={len(cleaned_text)}")
            resp = await self.llm.ainvoke(messages)
            response_text = (resp.content or "").strip()
            logger.debug(f"[Agent2][LLM] summarize out_len={len(response_text)}")

            if not response_text:
                raise ValueError("LLM returned empty response")

            parsed = self.json_parser.extract_json_from_text(response_text)
            used_fallback = False
            if not parsed:
                logger.warning("[Agent2] ⚠️ JSON parsing failed, using fallback summary")
                parsed = self.json_parser.create_fallback_summary(cleaned_text, max_segments=max_segments)
                used_fallback = True

            structured = self._validate_and_structure_summary(parsed, max_segments)

            elapsed = time.time() - start
            return SummarizeResponse(
                summary=structured["summary"],
                segments=structured["segments"],
                topics=structured["topics"],
                bullet_points=structured["bullet_points"],
                session_id=request.session_id,
                status="success",
                processing_time=elapsed,
                agent_info={
                    "agent": "agent2",
                    "input_length": len(cleaned_text),
                    "segments_generated": len(structured["segments"]),
                    "topics_identified": len(structured["topics"]),
                    "parsing_method": "fallback" if used_fallback else "json",
                },
            )
        except Exception as e:
            elapsed = time.time() - start
            error_msg = f"Summarization failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            return SummarizeResponse(
                summary="Error occurred during summarization",
                segments=[],
                topics=[],
                bullet_points=[],
                session_id=request.session_id,
                status="error",
                processing_time=elapsed,
                agent_info={"error": error_msg},
            )

    # --------- Chat ---------
    @retry(stop=stop_after_attempt(settings.MAX_RETRIES),
           wait=wait_exponential(multiplier=1, min=1, max=8))
    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        start = time.time()
        try:
            if not validate_session_id(request.session_id):
                raise ValueError("Invalid session_id")

            if await is_effectively_muted(request.session_id):
                logger.info(f"🔇 Session {request.session_id} muted (agent2); skipping chat")
                return ChatResponse(
                    response="",
                    audio_path=None,
                    session_id=request.session_id,
                    status="muted",
                    processing_time=0.0,
                    agent_info={"agent": "agent2", "muted": True},
                    turn_number=request.turn_number,
                )

            system_prompt = (
                "You are Agent2: an analytical expert and researcher in a podcast panel. "
                "Provide data-driven, concise (2–3 sentences) insights with a conversational tone."
            )
            if request.is_interruption:
                system_prompt += " This is a response to a user interruption; address it directly and succinctly."

            user_message = self._build_chat_user_message(request)
            messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
            logger.debug(f"[Agent2][LLM] chat ainvoke messages={len(messages)} text_len={len(request.text)}")
            resp = await self.llm.ainvoke(messages)
            response_text = (resp.content or "").strip()
            logger.debug(f"[Agent2][LLM] chat out_len={len(response_text)}")

            if not response_text:
                raise ValueError("LLM returned empty response")

            elapsed = time.time() - start
            return ChatResponse(
                response=response_text,
                audio_path=None,
                session_id=request.session_id,
                status="success",
                processing_time=elapsed,
                agent_info={
                    "agent": "agent2",
                    "conversation_mode": request.conversation_mode,
                    "is_interruption": request.is_interruption,
                    "voice": request.voice,
                },
                turn_number=request.turn_number,
            )
        except Exception as e:
            elapsed = time.time() - start
            error_msg = f"Chat processing failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            return ChatResponse(
                response=f"Error: {error_msg}",
                session_id=request.session_id,
                status="error",
                processing_time=elapsed,
                turn_number=request.turn_number,
            )

    # --------- Command ---------
    async def handle_command(self, request: CommandRequest) -> Dict[str, Any]:
        start = time.time()
        try:
            if not validate_session_id(request.session_id):
                return {"error": "Invalid session_id", "session_id": request.session_id}

            logger.info(f"🧭 Processing command for session {request.session_id}")
            command = request.command.strip()
            low = command.lower()

            if low.startswith("/analyze"):
                text = command[len("/analyze"):].strip()
                if not text:
                    return {"error": "No text provided for analysis", "session_id": request.session_id}
                analysis_request = SummarizeRequest(raw_script=text, session_id=request.session_id, max_segments=5)
                result = await self.summarize(analysis_request)
                return result.dict()

            elif low.startswith("/topics"):
                text = command[len("/topics"):].strip()
                if not text:
                    return {"error": "No text provided for topic extraction", "session_id": request.session_id}
                topics = await self._extract_topics(text)
                return {"topics": topics, "session_id": request.session_id, "status": "success"}

            elif low.startswith("/segments"):
                text = command[len("/segments"):].strip()
                if not text:
                    return {"error": "No text provided for segmentation", "session_id": request.session_id}
                segments = await self._create_segments(text)
                return {"segments": segments, "session_id": request.session_id, "status": "success"}

            else:
                return {
                    "error": "Unknown command",
                    "available_commands": ["/analyze", "/topics", "/segments"],
                    "usage": "Use /analyze <text>, /topics <text>, or /segments <text>",
                    "session_id": request.session_id,
                }

        except Exception as e:
            elapsed = time.time() - start
            error_msg = f"Command processing failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            return {
                "error": error_msg,
                "session_id": request.session_id,
                "processing_time": elapsed,
            }

    # --------- Helpers ---------
    def _build_chat_user_message(self, req: ChatRequest) -> str:
        base = [f"User message:\n{req.text}"]
        if req.turn_number and req.max_turns:
            base.append(f"Conversation turn: {req.turn_number} of {req.max_turns}.")
        if req.conversation_context:
            base.append(f"Recent conversation context:\n{req.conversation_context}")
        return "\n\n".join(base)

    def _validate_and_structure_summary(self, parsed: Dict[str, Any], max_segments: int) -> Dict[str, Any]:
        summary = parsed.get("summary", "Generated summary")
        segments = parsed.get("segments", [])
        topics = parsed.get("topics", [])
        bullet_points = parsed.get("bullet_points", [])

        cleaned_segments: List[Dict[str, Any]] = []
        if isinstance(segments, list):
            for i, seg in enumerate(segments[: max_segments]):
                if isinstance(seg, dict):
                    cleaned_segments.append(
                        {
                            "id": seg.get("id", i + 1),
                            "content": str(seg.get("content", "")),
                            "type": seg.get("type", "main_point"),
                            "timestamp": seg.get("timestamp", f"{i * 30}s"),
                        }
                    )

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
            messages = [SystemMessage(content="Return only a JSON array, no prose."), HumanMessage(content=prompt)]
            logger.debug(f"[Agent2][LLM] topics ainvoke text_len={len(text)}")
            resp = await self.llm.ainvoke(messages)
            response_text = (resp.content or "").strip()
            logger.debug(f"[Agent2][LLM] topics out_len={len(response_text)}")

            # Try parsing direct array
            try:
                parsed = json.loads(response_text)
                if isinstance(parsed, list):
                    return [str(t) for t in parsed[:8]]
            except Exception:
                pass

            # Use robust extractor fallback
            parsed2 = RobustJSONParser.extract_json_from_text(response_text)
            if isinstance(parsed2, list):
                return [str(t) for t in parsed2[:8]]
            return []
        except Exception as e:
            logger.warning(f"⚠️ Topic extraction failed: {e}")
            return []

    async def _create_segments(self, text: str) -> List[Dict[str, Any]]:
        try:
            prompt = f"""Break the text into 5–8 logical segments. RETURN ONLY a JSON array of segment objects.

Each segment object: {{"id":1,"content":"...","type":"main_point","timestamp":"00:30"}}

Text:
{text}"""
            messages = [SystemMessage(content="Return only a JSON array, no prose."), HumanMessage(content=prompt)]
            logger.debug(f"[Agent2][LLM] segments ainvoke text_len={len(text)}")
            resp = await self.llm.ainvoke(messages)
            response_text = (resp.content or "").strip()
            logger.debug(f"[Agent2][LLM] segments out_len={len(response_text)}")

            # Try parsing direct array
            try:
                parsed = json.loads(response_text)
            except Exception:
                parsed = RobustJSONParser.extract_json_from_text(response_text)

            if isinstance(parsed, list):
                segments: List[Dict[str, Any]] = []
                for i, seg in enumerate(parsed[:8]):
                    if isinstance(seg, dict):
                        segments.append(
                            {
                                "id": seg.get("id", i + 1),
                                "content": str(seg.get("content", "")),
                                "type": seg.get("type", "main_point"),
                                "timestamp": seg.get("timestamp", f"{i * 30}s"),
                            }
                        )
                return segments
            return []
        except Exception as e:
            logger.warning(f"⚠️ Segment creation failed: {e}")
            return []


# ========== FastAPI App ==========
app = FastAPI(
    title="📝 Agent2 - Summarizer & Analyst",
    description="Production-grade summarizer and analyst agent for podcast content",
    version="2.1.4",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize agent
try:
    agent = Agent2()
    logger.info("🚀 Agent2 service ready")
except Exception as e:
    logger.critical(f"❌ Failed to initialize Agent2: {e}")
    raise


# ---- Startup diagnostics (non-secret)
@app.on_event("startup")
async def startup():
    required = ["AZURE_OPENAI_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "OPENAI_API_VERSION", "ORCHESTRATOR_URL"]
    presence = {k: ("present" if os.getenv(k) else "MISSING") for k in required}
    print(f"[Agent2][STARTUP] Env presence: {presence}")
    print(f"[Agent2][STARTUP] ORCHESTRATOR_BASE={_orch_base()}")


# ---- Debug config endpoint (safe, no secrets)
from typing import List as _List
import os as _os

def _present(k: str) -> bool:
    v = _os.getenv(k, "")
    return bool(v and v.strip())

def _looks_url(k: str) -> bool:
    v = _os.getenv(k, "")
    return v.startswith("http://") or v.startswith("https://")

def attach_debug_config(app: FastAPI, service_name: str, required_vars: _List[str], url_vars: _List[str] = []):
    @app.get("/debug/config")
    def debug_config():
        ok = {}
        for k in required_vars:
            ok[k] = "present" if _present(k) else "MISSING"
        for k in url_vars:
            if _present(k):
                ok[k] = ok.get(k, "present")
            ok[f"{k}_is_url"] = "ok" if _looks_url(k) else "INVALID_URL"
        return {"service": service_name, "vars": ok}

# Attach
attach_debug_config(
    app,
    "agent2",
    required_vars=["AZURE_OPENAI_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "OPENAI_API_VERSION", "ORCHESTRATOR_URL"],
    url_vars=["ORCHESTRATOR_URL"]
)


# ========== API Endpoints ==========
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
        required = [
            bool(settings.AZURE_OPENAI_KEY),
            bool(settings.AZURE_OPENAI_ENDPOINT),
            bool(settings.AZURE_OPENAI_DEPLOYMENT),
        ]
        return {
            "status": "healthy" if all(required) else "degraded",
            "service": "Agent2 - Summarizer & Analyst",
            "version": "2.1.4",
            "uptime": time.time() - agent._start_time,
        }
    except Exception as e:
        logger.error(f"❌ Health endpoint error: {e}")
        return {"status": "unhealthy", "error": str(e)}

@app.get("/")
async def root():
    return {
        "service": "Agent2 - Summarizer & Analyst",
        "version": "2.1.4",
        "description": "Production-grade summarizer and analyst agent for podcast content",
        "endpoints": {
            "summarize": "/v1/summarize",
            "chat": "/v1/chat",
            "command": "/v1/command",
            "health": "/health",
            "debug/config": "/debug/config",
        },
        "features": [
            "Comprehensive summarization",
            "Topic extraction & segmentation",
            "Robust JSON parsing with fallbacks",
            "Explicit system prompts for consistency",
            "Retry logic & lightweight health",
            "Session ID validation",
        ],
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")
