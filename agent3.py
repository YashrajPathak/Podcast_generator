# agent3.py – 🔊 Production-Grade TTS Generator (AAD + Streaming + Per-Agent Mute) – v2.3.0

import os
import json
import time
import uuid
import asyncio
import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from dotenv import load_dotenv
load_dotenv()  # ensure .env is loaded even if CWD differs

import azure.cognitiveservices.speech as speechsdk
from azure.identity import ClientSecretCredential

import wave as wave_mod
from httpx import AsyncClient, Timeout
from tenacity import retry, stop_after_attempt, wait_exponential

# LangChain (non-deprecated)
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import AzureChatOpenAI

# ---------- Optional utils (preferred if present) ----------
try:
    from utils import (
        clean_text_for_processing,      # normalization + length cap
        validate_session_id,            # id guard
        is_session_muted as utils_is_session_muted,  # graph mute helper
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
    # Azure AD for Speech
    CLIENT_ID: str
    CLIENT_SECRET: str
    TENANT_ID: str
    RESOURCE_ID: str
    SPEECH_REGION: str = "eastus"

    # Azure OpenAI (used in /v1/chat)
    AZURE_OPENAI_KEY: str
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4o"
    OPENAI_API_VERSION: str = "2024-05-01-preview"

    # Service Integration
    ORCHESTRATOR_URL: str = "http://localhost:8008"  # Graph base (no /conversation)
    AGENT_TIMEOUT: float = 30.0
    MAX_RETRIES: int = 3

    # TTS / Files
    DEFAULT_VOICE: str = "en-US-AriaNeural"
    AUDIO_DIR: str = "audio_cache"
    MAX_TEXT_LENGTH: int = 5000
    MAX_CONCURRENT_SYNTHESIS: int = 3
    AUDIO_CACHE_TTL: int = 3600  # seconds (1h)

    # Voices cache
    VOICES_CACHE_TTL: int = 900  # 15 minutes

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


try:
    settings = Settings()
except Exception as e:
    print(f"❌ Agent3 Configuration Error: {e}")
    raise


# ========== Logging ==========
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Agent3")


# ---------- Orchestrator base normalizer ----------
def _orch_base() -> str:
    """Return normalized orchestrator base URL (no trailing slash and no /conversation)."""
    base = (settings.ORCHESTRATOR_URL or "http://localhost:8008").rstrip("/")
    if base.endswith("/conversation"):
        logger.warning("[Agent3] ORCHESTRATOR_URL ended with /conversation; stripping for API calls")
        base = base[:-len("/conversation")]
    return base


# ---------- FastAPI / CORS ----------
app = FastAPI(
    title="🔊 Agent3 - TTS Generator",
    description="AAD-auth Azure Speech TTS with streaming, per-agent mute checks, and chat support",
    version="2.3.0"
)
origins = [o.strip() for o in os.getenv("UI_ORIGINS", os.getenv("UI_ORIGIN", "http://localhost:8501")).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
    max_age=86400,
)


# ========== Models ==========
class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice: str = Field(default=settings.DEFAULT_VOICE)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)  # 0.5x–2.0x
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)  # 0.5–2.0; converted to %
    session_id: str = Field(default="default")


class TTSResponse(BaseModel):
    audio_path: Optional[str]
    audio_hex: Optional[str] = None  # ✅ inline hex for immediate playback
    duration: Optional[float] = None
    text_length: int = 0
    voice: str = settings.DEFAULT_VOICE
    session_id: str = "default"
    status: str = "success"
    processing_time: Optional[float] = None
    agent_info: Optional[Dict[str, Any]] = None
    speed: Optional[float] = None
    pitch: Optional[float] = None


class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    session_id: str = Field(default="default")
    voice: str = Field(default="en-IN-PrabhatNeural")
    conversation_mode: str = Field(default="agent_to_agent")
    is_conversation_turn: bool = Field(default=False)
    is_interruption: bool = Field(default=False)
    turn_number: Optional[int] = None
    max_turns: Optional[int] = None
    conversation_context: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    audio_path: Optional[str] = None
    audio_hex: Optional[str] = None  # optional for chat, too
    session_id: str
    status: str = "success"
    processing_time: Optional[float] = None
    agent_info: Optional[Dict[str, Any]] = None
    turn_number: Optional[int] = None


# ========== Auth Manager (AAD only, with caching) ==========
class AzureSpeechAuthManager:
    def __init__(self):
        self._auth_cache: Dict[str, speechsdk.SpeechConfig] = {}
        self._last_auth_time: Dict[str, float] = {}
        self._cache_ttl = 3600  # 1h

    def get_speech_config(self, voice: str = settings.DEFAULT_VOICE) -> speechsdk.SpeechConfig:
        cache_key = f"config:{voice}"
        now = time.time()
        if cache_key in self._auth_cache and now - self._last_auth_time.get(cache_key, 0) < self._cache_ttl:
            return self._auth_cache[cache_key]

        credential = ClientSecretCredential(
            tenant_id=settings.TENANT_ID,
            client_id=settings.CLIENT_ID,
            client_secret=settings.CLIENT_SECRET
        )
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        # NOTE: Using aad#RESOURCE_ID#token is a known working pattern in this project
        auth_token = f"aad#{settings.RESOURCE_ID}#{token.token}"

        speech_config = speechsdk.SpeechConfig(auth_token=auth_token, region=settings.SPEECH_REGION)
        speech_config.speech_synthesis_voice_name = voice

        self._auth_cache[cache_key] = speech_config
        self._last_auth_time[cache_key] = now
        logger.info(f"[Agent3][AUTH] ✅ AAD token acquired; voice={voice}")
        return speech_config

    def clear_cache(self):
        self._auth_cache.clear()
        self._last_auth_time.clear()
        logger.info("[Agent3][AUTH] 🗑️ Cleared speech auth cache")


# ========== TTS Manager ==========
class TTSManager:
    def __init__(self, auth_manager: AzureSpeechAuthManager):
        self.auth_manager = auth_manager
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_SYNTHESIS)
        self.audio_dir = Path(settings.AUDIO_DIR)
        try:
            self.audio_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[Agent3][TTS] ⚠️ could not create audio_dir={self.audio_dir}: {e}")

    def _make_ssml(self, text: str, voice: str, speed: float = 1.0, pitch: float = 1.0) -> str:
        """Generate SSML with natural speech patterns, with basic injection safety."""
        # Clamp params to natural ranges
        speed = max(0.8, min(speed, 1.2))  # 0.8x-1.2x speed
        pitch = max(0.8, min(pitch, 1.2))  # ~-20% to +20%

        # Sanitize: strip tags to prevent SSML injection
        safe = re.sub(r"<[^>]+>", "", text or "")
        # Remove any audio src-like patterns defensively
        safe = re.sub(r"\b(src|href)\s*=\s*['\"]?[^'\"\s>]+", "", safe, flags=re.IGNORECASE)

        # Natural pauses
        safe = re.sub(r'([.!?])', r'\1<break time="400ms"/>', safe)
        safe = re.sub(r'(,|;)', r'\1<break time="200ms"/>', safe)

        pct = int(round((pitch - 1.0) * 100))
        pitch_attr = f"{pct:+d}%"
        rate_attr = f"{int(round((speed - 1.0) * 100)):+d}%"

        ssml = f"""
        <speak version='1.0' xml:lang='en-US'>
            <voice name='{voice}'>
                <prosody rate='{rate_attr}' pitch='{pitch_attr}'>
                    {safe}
                </prosody>
            </voice>
        </speak>
        """.strip()
        return ssml

    async def synthesize_wav(self, text: str, voice: str, speed: float = 1.0, pitch: float = 1.0) -> Optional[str]:
        """Generate WAV file with natural pacing."""
        speech_config = self.auth_manager.get_speech_config(voice)
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )

        # Unique filename with voice prefix for debugging
        voice_prefix = voice.split("-")[-1].lower()[:4]
        filename = f"{voice_prefix}_{uuid.uuid4().hex}.wav"
        out_path = self.audio_dir / filename

        try:
            audio_config = speechsdk.audio.AudioOutputConfig(filename=str(out_path))
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=speech_config,
                audio_config=audio_config
            )

            ssml = self._make_ssml(text, voice, speed, pitch)
            result = await asyncio.to_thread(
                lambda: synthesizer.speak_ssml_async(ssml).get()
            )

            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                logger.info(f"[Agent3][TTS] ✅ Synth complete voice={voice} file={filename}")
                return str(out_path)

            if result.reason == speechsdk.ResultReason.Canceled:
                details = result.cancellation_details
                logger.error(f"[Agent3][TTS] ❌ Canceled: {details.reason} - {details.error_details}")
            else:
                logger.error(f"[Agent3][TTS] ❌ Failed: {result.reason}")

        except Exception as e:
            logger.error(f"[Agent3][TTS] ❌ Synthesis error: {str(e)}", exc_info=True)

        return None

    async def synthesize(self, req: TTSRequest) -> TTSResponse:
        """Main synthesis endpoint with enhanced validation."""
        start_time = time.time()
        async with self.semaphore:
            try:
                if not validate_session_id(req.session_id):
                    raise ValueError("Invalid session ID format")

                cleaned_text = clean_text_for_processing(req.text, settings.MAX_TEXT_LENGTH)
                if not cleaned_text.strip():
                    raise ValueError("Empty or invalid text after cleaning")

                wav_path = await self.synthesize_wav(cleaned_text, req.voice, req.speed, req.pitch)
                if not wav_path:
                    raise RuntimeError("Audio synthesis returned no path")

                duration = self._estimate_duration(wav_path)
                char_count = len(cleaned_text)
                words_per_min = int((char_count / 5) / (duration / 60)) if (duration and duration > 0) else 0

                return TTSResponse(
                    audio_path=wav_path,
                    duration=duration,
                    text_length=char_count,
                    voice=req.voice,
                    session_id=req.session_id,
                    status="success",
                    processing_time=round(time.time() - start_time, 3),
                    agent_info={
                        "agent": "agent3",
                        "ssml": True,
                        "words_per_min": words_per_min,
                        "speed_setting": req.speed,
                        "pitch_setting": req.pitch
                    },
                    speed=req.speed,
                    pitch=req.pitch,
                )

            except Exception as e:
                logger.error(f"[Agent3][TTS] ❌ processing failed: {str(e)}", exc_info=True)
                return TTSResponse(
                    audio_path=None,
                    text_length=len(getattr(req, "text", "")),
                    voice=getattr(req, "voice", settings.DEFAULT_VOICE),
                    session_id=getattr(req, "session_id", "default"),
                    status="error",
                    processing_time=round(time.time() - start_time, 3),
                    agent_info={
                        "error": str(e),
                        "input_sample": (req.text[:100] + "...") if getattr(req, "text", None) else None
                    }
                )

    def _estimate_duration(self, file_path: str) -> Optional[float]:
        """More accurate duration estimation from WAV header; fallback to size approximation."""
        try:
            with wave_mod.open(str(file_path), 'rb') as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return round(frames / float(rate), 2)
        except Exception as e:
            logger.debug(f"[Agent3][TTS] duration header read failed: {e}")
            try:
                size = os.path.getsize(file_path)
                # 24kHz * 16-bit mono ≈ 48KB/sec -> size/48000 ~ seconds (approx)
                return round(size / 48000.0, 2)
            except Exception as e2:
                logger.warning(f"[Agent3][TTS] ⚠️ duration estimation failed: {e2}")
                return None

    async def _periodic_cleanup(self):
        """Enhanced cleanup with size limits."""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                now = time.time()
                removed = 0
                total_size = 0

                files = sorted(self.audio_dir.glob("*.wav"), key=lambda f: f.stat().st_mtime)

                for fp in files:
                    try:
                        file_age = now - fp.stat().st_mtime
                        file_size = fp.stat().st_size

                        # Remove if older than TTL OR if cache exceeds ~100MB
                        if file_age > settings.AUDIO_CACHE_TTL or total_size > 100 * 1024 * 1024:
                            fp.unlink(missing_ok=True)
                            removed += 1
                        else:
                            total_size += file_size
                    except Exception as e:
                        logger.warning(f"[Agent3][CLEANUP] ⚠️ Failed to process {fp.name}: {e}")

                if removed:
                    logger.info(f"[Agent3][CLEANUP] 🗑️ Removed {removed} old files. Cache now ~{total_size/1024/1024:.1f}MB")
            except Exception as e:
                logger.error(f"[Agent3][CLEANUP] ❌ Cleanup cycle failed: {e}")
                await asyncio.sleep(60)  # wait longer on error


# ========== Per-agent mute helper ==========
@retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _fetch_mute_status_fallback(session_id: str) -> Optional[bool]:
    url = f"{_orch_base()}/session/{session_id}/mute-status"
    params = {"agent": "agent3"}
    async with AsyncClient(timeout=Timeout(settings.AGENT_TIMEOUT)) as client:
        r = await client.get(url, params=params)
        if r.status_code >= 500:
            raise RuntimeError(f"transient {r.status_code}")
        if r.status_code != 200:
            return None
        data = r.json()
        return bool(data.get("muted", False))


async def is_effectively_muted(session_id: Optional[str]) -> bool:
    """Check Graph /mute-status with agent=agent3 via utils; fallback to direct HTTP with retry."""
    if not session_id:
        return False
    try:
        return bool(await utils_is_session_muted(session_id, "agent3"))
    except Exception:
        pass
    try:
        res = await _fetch_mute_status_fallback(session_id)
        return bool(res) if res is not None else False
    except Exception as e:
        logger.warning(f"[Agent3][MUTE] fallback failed: {e}")
        return False


# ========== Voices Cache ==========
class VoicesCache:
    """Simple in-memory cache with TTL for Azure Speech voices."""
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._data: Optional[Tuple[float, List[Dict[str, Any]]]] = None
        self._lock = asyncio.Lock()

    async def get(self) -> Optional[List[Dict[str, Any]]]:
        async with self._lock:
            if not self._data:
                return None
            ts, voices = self._data
            if (time.time() - ts) > self.ttl:
                self._data = None
                return None
            return voices

    async def set(self, voices: List[Dict[str, Any]]):
        async with self._lock:
            self._data = (time.time(), voices)


voices_cache = VoicesCache(ttl_seconds=settings.VOICES_CACHE_TTL)


# ========== Agent ==========
class Agent3:
    def __init__(self):
        self._start_time = time.time()
        self.auth = AzureSpeechAuthManager()
        self.tts = TTSManager(self.auth)

        # LLM for /v1/chat (non-deprecated)
        self.llm = AzureChatOpenAI(
            deployment_name=settings.AZURE_OPENAI_DEPLOYMENT,
            api_key=settings.AZURE_OPENAI_KEY,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.OPENAI_API_VERSION,
            temperature=0.7,
            timeout=settings.AGENT_TIMEOUT
        )
        logger.info("[Agent3] ✅ initialized (LLM + TTS ready)")

    @retry(stop=stop_after_attempt(settings.MAX_RETRIES),
           wait=wait_exponential(multiplier=1, min=1, max=8))
    async def generate_audio(self, req: TTSRequest) -> TTSResponse:
        if not validate_session_id(req.session_id):
            return TTSResponse(
                audio_path=None,
                audio_hex=None,
                text_length=len(req.text),
                voice=req.voice,
                session_id=req.session_id,
                status="error",
                processing_time=0.0,
                agent_info={"error": "Invalid session_id"}
            )

        if await is_effectively_muted(req.session_id):
            logger.info(f"[Agent3] 🔇 session={req.session_id} muted; skip synthesis")
            return TTSResponse(
                audio_path=None,
                audio_hex=None,
                text_length=len(req.text),
                voice=req.voice,
                session_id=req.session_id,
                status="muted",
                processing_time=0.0
            )

        # Do synthesis (returns path)
        tts_resp = await self.tts.synthesize(req)

        # If success, add audio_hex
        if tts_resp and tts_resp.status == "success" and tts_resp.audio_path:
            try:
                fp = Path(tts_resp.audio_path)
                # make a public route path if raw path provided
                public_path = f"/audio/{fp.name}"
                with open(fp, "rb") as f:
                    audio_bytes = f.read()
                tts_resp.audio_hex = audio_bytes.hex()
                tts_resp.audio_path = public_path  # serve via endpoint
            except Exception as e:
                logger.warning(f"[Agent3] Could not read WAV to hex: {e}")

        return tts_resp

    def _build_conversation_system_prompt(self, request: ChatRequest) -> str:
        base = (
            "You are Agent3, a creative storyteller for a podcast panel.\n"
            "- Be vivid, concise (2–3 sentences), and engaging.\n"
            "- Use light humor and illustrative examples.\n"
            "- Fit the ongoing conversation naturally."
        )
        if request.is_interruption:
            base += "\n(Respond to an interruption; acknowledge and add color.)"
        if request.turn_number and request.max_turns:
            base += f"\n(This is turn {request.turn_number} of {request.max_turns}.)"
        if request.conversation_context:
            base += f"\nContext:\n{request.conversation_context}"
        return base

    @retry(stop=stop_after_attempt(settings.MAX_RETRIES),
           wait=wait_exponential(multiplier=1, min=1, max=8))
    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        start = time.time()
        try:
            if not validate_session_id(request.session_id):
                raise ValueError("Invalid session_id")

            sys_msg = SystemMessage(content=self._build_conversation_system_prompt(request))
            human = HumanMessage(content=request.text)
            resp = await self.llm.ainvoke([sys_msg, human])
            text = (resp.content or "").strip()
            if not text:
                raise ValueError("LLM returned empty response")

            audio_path = None
            audio_hex = None
            if not await is_effectively_muted(request.session_id):
                tts_resp = await self.tts.synthesize(
                    TTSRequest(text=text, voice=request.voice, session_id=request.session_id)
                )
                if tts_resp.status == "success" and tts_resp.audio_path:
                    fp = Path(tts_resp.audio_path)
                    audio_path = f"/audio/{fp.name}"
                    try:
                        with open(fp, "rb") as f:
                            audio_hex = f.read().hex()
                    except Exception as e:
                        logger.warning(f"[Agent3][CHAT] hex read failed: {e}")

            return ChatResponse(
                response=text,
                audio_path=audio_path,
                audio_hex=audio_hex,
                session_id=request.session_id,
                status="success",
                processing_time=round(time.time() - start, 3),
                agent_info={
                    "agent": "agent3",
                    "conversation_mode": request.conversation_mode,
                    "is_interruption": request.is_interruption,
                    "voice": request.voice,
                    "audio_generated": bool(audio_path)
                },
                turn_number=request.turn_number
            )
        except Exception as e:
            logger.error(f"[Agent3][CHAT] ❌ failed: {e}")
            return ChatResponse(
                response=f"Error: {e}",
                session_id=request.session_id,
                status="error",
                processing_time=round(time.time() - start, 3)
            )

    # For streaming: send PCM chunks via WebSocket
    async def ws_stream_text(self, text: str, voice: str, speed: float, pitch: float, websocket: WebSocket):
        cleaned = clean_text_for_processing(text, settings.MAX_TEXT_LENGTH)
        if not cleaned.strip():
            await websocket.send_json({"error": "empty text"})
            return

        speech_config = self.auth.get_speech_config(voice)
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
        ssml = self.tts._make_ssml(cleaned, voice, speed, pitch)

        result = await asyncio.to_thread(lambda: synthesizer.speak_ssml_async(ssml).get())
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            await websocket.send_json({"error": f"TTS failed: {result.reason}"})
            return

        stream = speechsdk.AudioDataStream(result)
        buffer = bytearray(4800)  # ~100ms chunks at 24kHz 16-bit mono
        while True:
            n = stream.read_data(buffer)
            if n == 0:
                break
            await websocket.send_bytes(buffer[:n])
        await websocket.send_json({"status": "completed", "voice": voice, "speed": speed, "pitch": pitch})


# ========== FastAPI App Lifecycle ==========
agent = Agent3()

@app.on_event("startup")
async def _start_cleanup():
    # Startup diagnostics
    presence = {k: ("present" if os.getenv(k) else "MISSING") for k in [
        "CLIENT_ID","CLIENT_SECRET","TENANT_ID","RESOURCE_ID","SPEECH_REGION",
        "AZURE_OPENAI_KEY","AZURE_OPENAI_ENDPOINT","AZURE_OPENAI_DEPLOYMENT","OPENAI_API_VERSION",
        "ORCHESTRATOR_URL"
    ]}
    print(f"[Agent3][STARTUP] Env presence: {presence}")
    print(f"[Agent3][STARTUP] ORCHESTRATOR_BASE={_orch_base()}")
    asyncio.create_task(agent.tts._periodic_cleanup())


# ========== API Endpoints ==========
@app.post("/generate-audio", response_model=TTSResponse)
async def generate_audio(request: TTSRequest):
    """JSON endpoint used by other agents: synthesize WAV and return path + inline hex."""
    tts_resp = await agent.generate_audio(request)
    return tts_resp


@app.post("/v1/chat", response_model=ChatResponse)
async def chat_handler(request: ChatRequest):
    """Generate a short spoken reply and (optionally) synthesize audio (with audio_hex)."""
    return await agent.handle_chat(request)


@app.get("/audio/{filename}")
async def get_audio(filename: str):
    """Serve audio files by name (with path traversal protection)."""
    base = Path(settings.AUDIO_DIR).resolve()
    fp = (base / filename).resolve()
    if base not in fp.parents and fp != base:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(path=str(fp), media_type="audio/wav", filename=fp.name)


# ---- AAD-compatible, retry-hardened voices route with caching ----
@app.get("/voices")
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=6))
async def get_voices(force_refresh: bool = Query(False, description="Bypass cache if true")):
    """List available Azure Speech voices (AAD auth) with in-memory TTL cache."""
    try:
        if not force_refresh:
            cached = await voices_cache.get()
            if cached is not None:
                return {
                    "status": "ok",
                    "default_voice": settings.DEFAULT_VOICE,
                    "count": len(cached),
                    "cached": True,
                    "voices": cached
                }

        credential = ClientSecretCredential(
            tenant_id=settings.TENANT_ID,
            client_id=settings.CLIENT_ID,
            client_secret=settings.CLIENT_SECRET
        )
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        auth_token = f"aad#{settings.RESOURCE_ID}#{token.token}"

        speech_config = speechsdk.SpeechConfig(auth_token=auth_token, region=settings.SPEECH_REGION)

        voices_result = await asyncio.to_thread(
            lambda: speechsdk.SpeechSynthesizer(speech_config=speech_config).get_voices_async().get()
        )

        voices = [
            {
                "shortName": v.short_name,
                "locale": getattr(v, "locale", None),
                "gender": getattr(v, "gender", None),
                "voiceType": getattr(v, "voice_type", None)
            }
            for v in (voices_result.voices or [])
        ]

        await voices_cache.set(voices)

        return {
            "status": "ok",
            "default_voice": settings.DEFAULT_VOICE,
            "count": len(voices),
            "cached": False,
            "ttl_seconds": settings.VOICES_CACHE_TTL,
            "voices": voices
        }
    except Exception as e:
        logger.error(f"[Agent3][VOICES] ❌ fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Unable to fetch voices")


@app.post("/clear-cache")
async def clear_cache():
    """Clear auth cache and delete cached audio WAVs."""
    try:
        agent.auth.clear_cache()
        count = 0
        for fp in Path(settings.AUDIO_DIR).glob("*.wav"):
            fp.unlink(missing_ok=True)
            count += 1
        await voices_cache.set([])  # clear voices cache
        return {"status": "success", "message": f"Cache cleared ({count} files)", "voices_cache_cleared": True}
    except Exception as e:
        logger.error(f"[Agent3][CACHE] ❌ clear failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/stream-audio")
async def ws_stream_audio(ws: WebSocket):
    """WebSocket: send PCM WAV chunks for live playback."""
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            text = data.get("text", "") or ""
            voice = data.get("voice", settings.DEFAULT_VOICE)
            session_id = data.get("session_id", "default")
            speed = float(data.get("speed", 1.0))
            pitch = float(data.get("pitch", 1.0))

            if await is_effectively_muted(session_id):
                await ws.send_json({"status": "muted"})
                continue

            await agent.ws_stream_text(text=text, voice=voice, speed=speed, pitch=pitch, websocket=ws)
    except WebSocketDisconnect:
        logger.info("[Agent3][WS] disconnected")
    except Exception as e:
        logger.error(f"[Agent3][WS] error: {e}")
        try:
            await ws.send_json({"error": str(e)})
        except Exception:
            pass


@app.get("/health")
async def health():
    """Health check with basic environment status."""
    try:
        cached_voices = await voices_cache.get()
        cached_count = len(list(Path(settings.AUDIO_DIR).glob("*.wav")))
        return {
            "status": "healthy",
            "service": "Agent3 - TTS Generator",
            "version": "2.3.0",
            "uptime_sec": round(time.time() - agent._start_time, 2),
            "audio_cache_dir": str(Path(settings.AUDIO_DIR).resolve()),
            "cached_files": cached_count,
            "voices_cached": (len(cached_voices) if cached_voices is not None else 0),
            "voices_cache_ttl": settings.VOICES_CACHE_TTL
        }
    except Exception as e:
        logger.error(f"[Agent3][HEALTH] failed: {e}")
        return {"status": "unhealthy", "error": str(e)}


@app.get("/")
async def root():
    return {
        "service": "Agent3 - TTS Generator",
        "version": "2.3.0",
        "endpoints": {
            "generate_audio": "/generate-audio",
            "chat": "/v1/chat",
            "ws_stream": "/ws/stream-audio",
            "get_audio": "/audio/{filename}",
            "voices": "/voices",
            "clear_cache": "/clear-cache",
            "health": "/health",
            "debug_config": "/debug/config"
        },
        "features": [
            "Azure AD Speech (no subscription key)",
            "JSON /generate-audio returns audio_path + audio_hex ✅",
            "WAV output + file serving",
            "WebSocket streaming (PCM WAV chunks)",
            "Per-agent mute integration with orchestrator",
            "LLM-assisted /v1/chat with optional TTS (also returns audio_hex)",
            "Cache clearing + periodic cleanup (startup scheduled)",
            "Path traversal protection on file serving",
            "Retries + timeouts on critical paths",
            "In-memory voices cache with TTL"
        ]
    }


# ---- Debug config endpoint (safe, no secrets)
  # alias to avoid shadowing
from typing import List as _List  # re-import cleanly

def _present(k: str) -> bool:
    v = os.getenv(k, "")
    return bool(v and v.strip())

def _looks_url(k: str) -> bool:
    v = os.getenv(k, "")
    return v.startswith("http://") or v.startswith("https://")

def attach_debug_config(app: FastAPI, service_name: str, required_vars: _List[str], url_vars: _List[str] = []):
    @app.get("/debug/config")
    def debug_config():
        ok: Dict[str, str] = {}
        for k in required_vars:
            ok[k] = "present" if _present(k) else "MISSING"
        for k in url_vars:
            if _present(k):
                ok[k] = ok.get(k, "present")
            ok[f"{k}_is_url"] = "ok" if _looks_url(k) else "INVALID_URL"
        return {"service": service_name, "vars": ok}

# Attach with correct var names (✅ ORCHESTRATOR_URL instead of old GRAPH_URL)
attach_debug_config(
    app, "agent3",
    required_vars=[
        "CLIENT_ID","CLIENT_SECRET","TENANT_ID","RESOURCE_ID","SPEECH_REGION",
        "AZURE_OPENAI_KEY","AZURE_OPENAI_ENDPOINT","AZURE_OPENAI_DEPLOYMENT","OPENAI_API_VERSION",
        "ORCHESTRATOR_URL"
    ],
    url_vars=["ORCHESTRATOR_URL"]
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent3:app", host="0.0.0.0", port=8004, log_level="info", reload=False)
