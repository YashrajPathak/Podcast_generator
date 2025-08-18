# graph.py - Hybrid Podcast Orchestrator (v3.4 with TTS fallback)
import os
import time
import json
import uuid
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import wave as wav
from io import BytesIO
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse

from fastapi import (
    FastAPI, HTTPException, Query, Body, WebSocket,
    WebSocketDisconnect, Request, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

# ========= Robust logging (Windows console safe) =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - graph - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("graph.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# Make console handler UTF-8 tolerant on Windows
try:
    import sys, io
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler) and hasattr(sys.stdout, "buffer"):
            try:
                h.setStream(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace"))
            except Exception:
                pass
except Exception:
    pass

logger = logging.getLogger("Graph")

# ========= Settings =========
class Settings(BaseSettings):
    REGISTRY_PATH: str = "agent_registry.json"
    AGENT1_URL: str = "http://localhost:8001"
    AGENT2_URL: str = "http://localhost:8002"
    AGENT3_URL: str = "http://localhost:8004"
    AGENT4_URL: str = "http://localhost:8006"
    AGENT5_URL: str = "http://localhost:8007"
    HOST: str = "0.0.0.0"
    PORT: int = 8008
    AGENT_TIMEOUT: float = 30.0
    MAX_TURNS_PER_ROUND: int = 5
    MAX_RETRIES: int = 3
    DEFAULT_AGENTS: List[str] = ["agent1", "agent2", "agent3"]
    DEFAULT_VOICES: Dict[str, str] = {
        "agent1": "en-US-AriaNeural",
        "agent2": "en-GB-RyanNeural",
        "agent3": "en-IN-PrabhatNeural",
        "agent4": "en-US-GuyNeural",
        "agent5": "en-US-JennyNeural",
    }
    STATE_DIR: str = "graph_state"
    AUDIO_CHUNK_MS: int = 100       # precise chunk duration
    SAMPLE_RATE: int = 24000
    SAMPLE_WIDTH: int = 2           # 16-bit audio
    HEALTH_CHECK_RATE_LIMIT: str = "10/minute"
    FINAL_AUDIO_DIR: str = "audio_cache"

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        extra = "ignore"

settings = Settings()

# ========= Initialization =========
app = FastAPI(
    title="Hybrid Podcast Orchestrator",
    description="Combines real-time audio mixing with persistent multi-agent conversations",
    version="3.4"
)

# Middleware (CORS)
origins = [o.strip() for o in os.getenv("UI_ORIGINS", os.getenv("UI_ORIGIN", "http://localhost:8501")).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
    max_age=86400,
)

# ========= Rate Limiting with Fallback =========
class DummyLimiter:
    def limit(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.middleware import SlowAPIMiddleware

    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    logger.info("Rate limiting enabled")
except ImportError:
    logger.warning("SlowAPI not installed, rate limiting disabled")
    limiter = DummyLimiter()
except Exception as e:
    logger.warning(f"Rate limiter initialization failed: {e}")
    limiter = DummyLimiter()

# ========= Audio Mixer (kept; not used for live stream, used for finalization fallback) =========
class AudioMixer:
    def __init__(self):
        self.active_streams: Dict[str, bytes] = {}
        self.mix_buffer = bytearray()

    def add_stream(self, agent_id: str, audio_data: bytes):
        self.active_streams[agent_id] = audio_data
        self.remix()

    def remove_stream(self, agent_id: str):
        if agent_id in self.active_streams:
            del self.active_streams[agent_id]
            self.remix()

    def remix(self):
        self.mix_buffer = bytearray()
        streams = list(self.active_streams.values())
        if not streams:
            return
        max_len = max(len(s) for s in streams)
        for i in range(0, max_len, settings.SAMPLE_WIDTH):
            samples = []
            for stream in streams:
                if i < len(stream):
                    sample = int.from_bytes(stream[i:i+settings.SAMPLE_WIDTH], 'little', signed=True)
                    samples.append(sample)
            if samples:
                mixed = sum(samples) // len(samples)
                self.mix_buffer.extend(mixed.to_bytes(settings.SAMPLE_WIDTH, 'little', signed=True))

    def get_mixed_audio(self) -> bytes:
        return bytes(self.mix_buffer)

mixer = AudioMixer()

# ========= WebSocket Connection Manager =========
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections.setdefault(session_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, session_id: str):
        conns = self.active_connections.get(session_id)
        if not conns:
            return
        try:
            conns.remove(websocket)
        except ValueError:
            pass

    async def broadcast_audio(self, session_id: str, audio_data: bytes):
        conns = self.active_connections.get(session_id, [])
        for ws in list(conns):
            try:
                await ws.send_bytes(audio_data)
            except Exception:
                self.disconnect(ws, session_id)

manager = ConnectionManager()

# ========= AudioStreamManager (FIXED: PCM chunking & WAV decode) =========
class AudioStreamManager:
    def __init__(self):
        self.active_streams: Dict[str, bool] = {}
        # bytes per 100ms = sr * width * 0.1
        self.chunk_bytes = int(settings.SAMPLE_RATE * settings.SAMPLE_WIDTH * (settings.AUDIO_CHUNK_MS / 1000.0))

    def _wav_to_pcm(self, wav_bytes: bytes) -> Tuple[bytes, int, int]:
        """Decode WAV -> PCM bytes; return (pcm, rate, width). If not WAV, return as-is with defaults."""
        if not wav_bytes or len(wav_bytes) < 12:
            return b"", settings.SAMPLE_RATE, settings.SAMPLE_WIDTH
        # Check RIFF header
        if wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
            # Assume already PCM; stream as-is
            return wav_bytes, settings.SAMPLE_RATE, settings.SAMPLE_WIDTH
        try:
            with wav.open(BytesIO(wav_bytes), 'rb') as wf:
                rate = wf.getframerate()
                width = wf.getsampwidth()
                frames = wf.readframes(wf.getnframes())
                return frames, rate, width
        except Exception:
            # If decode fails, stream raw
            return wav_bytes, settings.SAMPLE_RATE, settings.SAMPLE_WIDTH

    async def stream_audio(self, session_id: str, audio_data: bytes):
        """Stream PCM audio in precise chunks. Accepts WAV bytes and converts to PCM."""
        if not audio_data:
            return

        if session_id not in self.active_streams:
            self.active_streams[session_id] = True

        pcm, rate, width = self._wav_to_pcm(audio_data)

        chunk_bytes = int(rate * width * (settings.AUDIO_CHUNK_MS / 1000.0))
        chunk_bytes = max(chunk_bytes, 1)

        await asyncio.sleep(0.05)
        try:
            for i in range(0, len(pcm), chunk_bytes):
                if not self.active_streams.get(session_id):
                    break
                chunk = pcm[i:i + chunk_bytes]
                await manager.broadcast_audio(session_id, chunk)
                await asyncio.sleep(settings.AUDIO_CHUNK_MS / 1000.0)
        finally:
            await asyncio.sleep(0.05)

    def activate(self, session_id: str):
        self.active_streams[session_id] = True

    def deactivate(self, session_id: str):
        if session_id in self.active_streams:
            self.active_streams[session_id] = False
            del self.active_streams[session_id]

stream_manager = AudioStreamManager()

# ========= Registry =========
def _load_registry() -> Dict[str, Dict[str, Any]]:
    reg: Dict[str, Dict[str, Any]] = {}
    path = Path(settings.REGISTRY_PATH)
    if path.exists():
        try:
            reg = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to parse {settings.REGISTRY_PATH}: {e}")
    reg.setdefault("agent1", {"name": "Host", "url": settings.AGENT1_URL, "role": "host/moderator"})
    reg.setdefault("agent2", {"name": "Analyst", "url": settings.AGENT2_URL, "role": "analyst/researcher"})
    reg.setdefault("agent3", {"name": "Storyteller", "url": settings.AGENT3_URL, "role": "storyteller/entertainer"})
    reg.setdefault("agent4", {"name": "Export", "url": settings.AGENT4_URL, "role": "technical/fact-checker"})
    reg.setdefault("agent5", {"name": "Logger", "url": settings.AGENT5_URL, "role": "philosopher/deep thinker"})
    return reg

REGISTRY = _load_registry()

def _chat_url(agent_key: str) -> Optional[str]:
    info = REGISTRY.get(agent_key)
    if not info: return None
    base = str(info.get("url", "")).rstrip("/")
    return f"{base}/v1/chat" if base else None

def _agent_role(agent_key: str) -> str:
    info = REGISTRY.get(agent_key) or {}
    return str(info.get("role") or agent_key)

# ========= Models =========
class RunEvent(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    event: str = Field(..., description="start|pause|resume|end|topic_input|interrupt|message")
    input: Optional[str] = None
    agents: Optional[List[str]] = None
    voices: Optional[Dict[str,str]] = None
    max_turns: Optional[int] = None
    target_minutes: Optional[int] = Field(default=None, ge=0, le=10)

class Turn(BaseModel):
    turn_id: int
    agent: str
    role: str
    response: str
    audio_path: Optional[str] = None
    audio_data: bytes = b""
    timestamp: float

class SessionState(BaseModel):
    session_id: str
    topic: Optional[str] = None
    created_at: float
    updated_at: float
    paused: bool = False
    ended: bool = False
    agent_order: List[str] = []
    voices: Dict[str,str] = {}
    mute: Dict[str,bool] = {}
    current_round: int = 0
    max_turns_per_round: int = settings.MAX_TURNS_PER_ROUND
    history: List[Turn] = []
    audio_broadcast_task: Optional[Any] = None
    target_ms: int = 0
    audio_ms: int = 0
    ended_reason: Optional[str] = None
    turn_audio: Dict[int, Dict[str, Any]] = {}
    final_wav: Optional[str] = None
    deadline_ts: Optional[float] = None

    class Config:
        arbitrary_types_allowed = True

    @field_validator('audio_broadcast_task')
    def validate_task(cls, v):
        if v is not None and not isinstance(v, asyncio.Task):
            raise ValueError("Must be an asyncio.Task or None")
        return v

# ========= Persistence =========
STATE_DIR = Path(settings.STATE_DIR); STATE_DIR.mkdir(parents=True, exist_ok=True)
FINAL_DIR = Path(settings.FINAL_AUDIO_DIR); FINAL_DIR.mkdir(parents=True, exist_ok=True)

def _session_path(sid: str) -> Path:
    return STATE_DIR / f"{sid}.json"

def _save_session(state: SessionState) -> None:
    try:
        state_dict = state.dict(exclude={"audio_broadcast_task"})
        for turn in state_dict["history"]:
            turn["audio_data"] = b""
        _session_path(state.session_id).write_text(
            json.dumps(state_dict, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"failed to persist {state.session_id}: {e}")

def _load_session(sid: str) -> Optional[SessionState]:
    p = _session_path(sid)
    if not p.exists(): return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return SessionState(**data)
    except Exception as e:
        logger.warning(f"failed to load {sid}: {e}")
        return None

def _list_sessions_meta() -> List[Dict[str, Any]]:
    out = []
    for p in STATE_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "session_id": d.get("session_id"),
                "topic": d.get("topic"),
                "created_at": d.get("created_at"),
                "updated_at": d.get("updated_at"),
                "paused": d.get("paused"),
                "ended": d.get("ended"),
                "agents": d.get("agent_order", []),
                "turns": len(d.get("history", []))
            })
        except Exception:
            pass
    out.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
    return out

# ========= Store =========
class MemoryStore:
    def __init__(self):
        self.sessions: Dict[str, SessionState] = {}
        self._lock = asyncio.Lock()

    async def get(self, sid: str) -> Optional[SessionState]:
        st = self.sessions.get(sid)
        if st: return st
        disk = _load_session(sid)
        if disk: self.sessions[sid] = disk
        return disk

    async def upsert(self, state: SessionState):
        async with self._lock:
            self.sessions[state.session_id] = state
            _save_session(state)

    async def ensure(self, sid: str) -> SessionState:
        st = await self.get(sid)
        if st: return st
        st = SessionState(
            session_id=sid,
            created_at=time.time(),
            updated_at=time.time(),
            agent_order=list(settings.DEFAULT_AGENTS),
            voices=dict(settings.DEFAULT_VOICES),
            mute={k: False for k in REGISTRY.keys()},
            history=[],
            current_round=0,
            max_turns_per_round=settings.MAX_TURNS_PER_ROUND
        )
        await self.upsert(st)
        return st

store = MemoryStore()

# ========= Finalize / Audio helpers =========
import base64
from dotenv import load_dotenv
load_dotenv(override=False)

async def _finalize_audio(state: SessionState) -> Optional[str]:
    try:
        if state.final_wav and Path(state.final_wav).exists():
            return state.final_wav

        out_path = FINAL_DIR / f"{state.session_id}.wav"

        # 1) Try mixed audio buffer
        mixed = mixer.get_mixed_audio()
        if mixed:
            with wav.open(str(out_path), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(settings.SAMPLE_WIDTH)
                wf.setframerate(settings.SAMPLE_RATE)
                wf.writeframes(mixed)
            state.final_wav = str(out_path)
            state.updated_at = time.time()
            await store.upsert(state)
            try:
                size = out_path.stat().st_size
                logger.info(f"finalize_audio session_id={state.session_id} file={out_path} bytes={size} source=mixer")
            except Exception:
                pass
            return str(out_path)

        # 2) Stitch per-turn clips
        ordered = [state.turn_audio[k] for k in sorted(state.turn_audio.keys()) if isinstance(state.turn_audio.get(k), dict)]
        clip_paths = [d.get("path") for d in ordered if d.get("path")]
        if clip_paths:
            stitched = _stitch_wavs(clip_paths, str(out_path))
            if stitched:
                state.final_wav = stitched
                state.updated_at = time.time()
                await store.upsert(state)
                try:
                    size = Path(stitched).stat().st_size if Path(stitched).exists() else 0
                    logger.info(f"finalize_audio session_id={state.session_id} file={stitched} bytes={size} source=stitch clips={len(clip_paths)}")
                except Exception:
                    pass
                return stitched

        logger.info(f"finalize_audio_no_data session_id={state.session_id}")
        return None
    except Exception as e:
        logger.warning(f"finalize_audio failed for {state.session_id}: {e}")
        return None

def _normalize_audio(audio_data: bytes) -> bytes:
    return audio_data

def _wav_ms(file_path: Optional[str]) -> int:
    if not file_path:
        return 0
    try:
        with wav.open(str(file_path), 'rb') as f:
            frames = f.getnframes()
            rate = f.getframerate()
            return int((frames / float(rate)) * 1000)
    except Exception:
        return 0

def _stitch_wavs(paths: List[str], out_path: str) -> Optional[str]:
    if not paths: return None
    try:
        with wav.open(paths[0], 'rb') as src0:
            n_channels = src0.getnchannels()
            sampwidth = src0.getsampwidth()
            framerate = src0.getframerate()
        with wav.open(out_path, 'wb') as out:
            out.setnchannels(n_channels)
            out.setsampwidth(sampwidth)
            out.setframerate(framerate)
            for p in paths:
                try:
                    with wav.open(p, 'rb') as s:
                        if (s.getnchannels() != n_channels or
                            s.getsampwidth() != sampwidth or
                            s.getframerate() != framerate):
                            continue
                        out.writeframes(s.readframes(s.getnframes()))
                except Exception:
                    continue
        return out_path
    except Exception:
        return None

# ---- NEW: TTS fallback via Agent3 (returns bytes) ----
async def _tts_fallback_bytes(text: str, voice: str, session_id: str) -> bytes:
    """If an agent returns text without audio, synthesize it using Agent3 and return WAV bytes."""
    if not text:
        return b""
    try:
        a3_base = (REGISTRY.get("agent3", {}) or {}).get("url") or settings.AGENT3_URL
        a3_base = str(a3_base).rstrip("/")
        url = f"{a3_base}/generate-audio"
        payload = {"text": text, "voice": voice, "session_id": session_id, "speed": 1.0, "pitch": 1.0}

        async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT) as c:
            r = await c.post(url, json=payload)
        if r.status_code != 200:
            logger.warning(f"tts_fallback agent3 non-200: {r.status_code}")
            return b""

        data = r.json() or {}

        # 1) Prefer inline hex
        hex_data = (data.get("audio_hex") or "")
        if isinstance(hex_data, str) and hex_data:
            try:
                return bytes.fromhex(hex_data)
            except Exception as e:
                logger.warning(f"tts_fallback hex decode failed: {e}")

        # 2) Fetch by path/URL
        ap = data.get("audio_path")
        if isinstance(ap, str) and ap:
            try:
                # normalize for Windows-style paths
                ap_norm = ap.replace("\\", "/")

                # Absolute URL
                if ap_norm.startswith(("http://", "https://")):
                    async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT) as c:
                        r2 = await c.get(ap_norm)
                    if r2.status_code == 200 and r2.content:
                        return r2.content

                # Relative path (serve-from-agent)
                fetch_url = f"{a3_base}/{ap_norm.lstrip('/')}"
                async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT) as c:
                    r3 = await c.get(fetch_url)
                if r3.status_code == 200 and r3.content:
                    return r3.content
                else:
                    logger.warning(f"tts_fallback fetch failed {fetch_url} -> {r3.status_code}")
            except Exception as e:
                logger.warning(f"tts_fallback fetch exception for {ap}: {e}")

    except Exception as e:
        logger.warning(f"tts_fallback error: {e}")

    return b""

def _write_local_wav(audio_bytes: bytes, agent_key: str) -> Optional[str]:
    """Persist bytes to a local WAV under audio_cache and return the path."""
    if not audio_bytes:
        return None
    try:
        FINAL_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    fname = f"{agent_key}_{uuid.uuid4().hex}.wav"
    out_path = FINAL_DIR / fname
    try:
        out_path.write_bytes(audio_bytes)
        return str(out_path)
    except Exception as e:
        logger.warning(f"write_local_wav failed: {e}")
        return None

# Background broadcaster (kept for future mixer streaming)
async def audio_broadcaster(session_id: str):
    try:
        while stream_manager.active_streams.get(session_id, False):
            try:
                data = mixer.get_mixed_audio()
                if data:
                    await stream_manager.stream_audio(session_id, data)
            except Exception:
                pass
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        return
    except Exception:
        return

# ========= Agent Communication =========
_client = httpx.AsyncClient

@retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _agent_chat(
    agent_key: str,
    *, 
    text: str,
    session_id: str,
    voice: str,
    turn_number: Optional[int],
    max_turns: Optional[int],
    conversation_context: Optional[str],
    is_interruption: bool
) -> Tuple[str, bytes, Optional[str]]:
    """
    Call an agent's /v1/chat endpoint and return (response_text, audio_bytes, audio_path_returned).
    Robustly extracts audio by preferring inline hex, then local file, then HTTP/relative fetch.
    """
    url = _chat_url(agent_key)
    if not url:
        raise RuntimeError(f"No chat URL for {agent_key}")

    payload = {
        "text": text,
        "session_id": session_id,
        "voice": voice,
        "conversation_mode": "agent_to_agent",
        "is_conversation_turn": True,
        "is_interruption": is_interruption,
        "turn_number": turn_number,
        "max_turns": max_turns,
        "conversation_context": conversation_context,
        "return_audio": True,
    }

    # --- POST to agent ---
    async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT) as client:
        r = await client.post(url, json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"{agent_key} chat failed ({r.status_code}): {r.text}")
    data = r.json() or {}

    # --- Extract text ---
    response_text = (data.get("response") or "").strip()

    # --- Extract audio bytes robustly ---
    audio_hex = (data.get("audio_hex") or "") or ""
    audio_path = data.get("audio_path")
    audio_data: bytes = b""

    # 1) Inline hex wins
    if isinstance(audio_hex, str) and audio_hex:
        try:
            audio_data = bytes.fromhex(audio_hex)
        except Exception as e:
            logger.warning(f"_agent_chat: hex decode failed for {agent_key}: {e}")
            audio_data = b""

    # 2) Local filesystem path
    if (not audio_data) and isinstance(audio_path, str) and audio_path:
        try:
            p = Path(str(audio_path))
            if p.exists():
                audio_data = p.read_bytes()
        except Exception:
            audio_data = b""

    # 3) Absolute HTTP(S) URL
    if (not audio_data) and isinstance(audio_path, str) and audio_path.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT) as c:
                r2 = await c.get(audio_path)
            if r2.status_code == 200 and r2.content:
                audio_data = r2.content
            else:
                logger.warning(f"_agent_chat: HTTP fetch failed {audio_path} -> {r2.status_code}")
        except Exception as e:
            logger.warning(f"_agent_chat: HTTP fetch exception for {audio_path}: {e}")
            audio_data = b""

    # 4) Relative path (construct from agent base URL)
    if (not audio_data) and isinstance(audio_path, str) and audio_path and not audio_path.startswith(("http://", "https://")):
        try:
            # Derive base from the chat URL: ".../v1/chat" -> base service URL
            base = url.rsplit("/v1/chat", 1)[0].rstrip("/")
            fetch_url = f"{base}/{audio_path.lstrip('/')}"
            async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT) as c:
                r3 = await c.get(fetch_url)
            if r3.status_code == 200 and r3.content:
                audio_data = r3.content
            else:
                logger.warning(f"_agent_chat: relative fetch failed {fetch_url} -> {r3.status_code}")
        except Exception as e:
            logger.warning(f"_agent_chat: relative fetch exception for {audio_path}: {e}")
            audio_data = b""

    # --- Log + return ---
    logger.info(
        f"agent_turn_ok session_id={session_id} agent={agent_key} "
        f"text_len={len(text)} audio_bytes={len(audio_data)}"
    )
    return response_text, _normalize_audio(audio_data), audio_path
# ========= Orchestration Core =========
async def _round_robin_once(state: SessionState, user_context: Optional[str] = None, is_interrupt: bool = False):
    if state.ended or state.paused:
        return
    try:
        if state.deadline_ts and time.time() >= state.deadline_ts and not state.ended:
            await _end_session(state, "deadline_reached")
            return
    except Exception:
        pass

    tail = state.history[-3:] if len(state.history) > 0 else []
    tail_text = "\n".join([f"{t.agent}: {t.response}" for t in tail])
    base_ctx = ""
    if state.topic:
        base_ctx += f"Topic: {state.topic}\n"
    if tail_text:
        base_ctx += f"Recent:\n{tail_text}\n"
    if user_context:
        base_ctx += f"User: {user_context}\n"

    max_turns = state.max_turns_per_round
    for idx, agent_key in enumerate(state.agent_order, start=1):
        if state.ended or state.paused:
            break
        if state.mute.get(agent_key, False):
            continue

        voice = state.voices.get(agent_key, settings.DEFAULT_VOICES.get(agent_key, "en-US-AriaNeural"))
        prompt = "Continue the panel discussion based on the topic and context above."

        local_audio_bytes = b""
        local_audio_path: Optional[str] = None

        try:
            text, audio_data, _remote_path = await _agent_chat(
                agent_key,
                text=prompt,
                session_id=state.session_id,
                voice=voice,
                turn_number=idx,
                max_turns=max_turns,
                conversation_context=base_ctx if base_ctx else None,
                is_interruption=is_interrupt
            )

            # If the agent returned text but no audio, synthesize via Agent3
            if (not audio_data) and text:
                audio_data = await _tts_fallback_bytes(text, voice, state.session_id)

            # Ensure we have a local wav file for this turn
            if audio_data:
                local_audio_bytes = audio_data
                local_audio_path = _write_local_wav(audio_data, agent_key)

            # Stream out this agent's audio (PCM-chunked) using the bytes we will persist
            await stream_manager.stream_audio(state.session_id, local_audio_bytes or audio_data)

        except Exception as e:
            logger.warning(f"{agent_key} turn failed: {e}")
            text, local_audio_bytes, local_audio_path = f"(error: {e})", b"", None

        turn = Turn(
            turn_id=(state.history[-1].turn_id + 1) if state.history else 1,
            agent=agent_key,
            role=_agent_role(agent_key),
            response=text,
            audio_path=local_audio_path,           # <-- always local path or None
            audio_data=local_audio_bytes,          # kept empty on disk by _save_session
            timestamp=time.time()
        )
        state.history.append(turn)

        # Track clip metadata for auto-play queue (even if missing -> skipped)
        try:
            if turn.audio_path and Path(turn.audio_path).exists():
                ms = _wav_ms(turn.audio_path)
                state.turn_audio[turn.turn_id] = {
                    "path": turn.audio_path,
                    "ms": ms,
                    "speaker": turn.agent,
                    "played": False,   # ensure auto-play queue sees it as unplayed
                }
                state.audio_ms += ms
                logger.info(
                    f"turn_done session_id={state.session_id} turn_id={turn.turn_id} agent={turn.agent} ms={ms} "
                    f"audio_ms={state.audio_ms} target_ms={state.target_ms} final_wav={state.final_wav}"
                )
            else:
                # still append entry to preserve ordering (marked played to skip)
                state.turn_audio[turn.turn_id] = {
                    "path": None,
                    "ms": 0,
                    "speaker": turn.agent,
                    "played": True
                }
        except Exception:
            pass

        state.updated_at = time.time()
        await store.upsert(state)

        if state.target_ms and state.audio_ms >= state.target_ms and not state.ended:
            await _end_session(state, "target_duration_reached")
            break

    state.current_round += 1

# ========= Session Ending Helper =========
async def _end_session(state: SessionState, reason: str = "unspecified"):
    """Mark a session as ended, deactivate streaming, and persist."""
    try:
        if state.ended:
            return
        state.ended = True
        state.ended_reason = reason
        state.updated_at = time.time()

        # deactivate live stream
        stream_manager.deactivate(state.session_id)

        # cancel broadcaster task if still running
        if state.audio_broadcast_task and not state.audio_broadcast_task.done():
            state.audio_broadcast_task.cancel()

        # finalize audio if possible
        try:
            await _finalize_audio(state)
        except Exception as e:
            logger.warning(f"end_session finalize_audio failed: {e}")

        await store.upsert(state)
        logger.info(f"end_session_ok session_id={state.session_id} reason={reason}")
    except Exception as e:
        logger.error(f"end_session_error session_id={getattr(state,'session_id','?')} err={e}")

# ========= JSON Safety Helper =========
from typing import Any as _Any
def _to_json_safe(obj: _Any) -> _Any:
    try:
        if isinstance(obj, (bytes, bytearray, memoryview)):
            try:
                return {"__type__": "bytes", "b64": base64.b64encode(bytes(obj)).decode("ascii")}
            except Exception:
                return {"__type__": "bytes", "len": len(obj)}
        if isinstance(obj, dict):
            return {k: _to_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_to_json_safe(v) for v in obj]
        return obj
    except Exception:
        return str(obj)

# ========= API Endpoints =========
@app.websocket("/ws/audio/{session_id}")
async def websocket_audio(websocket: WebSocket, session_id: str):
    try:
        await manager.connect(websocket, session_id)
        while True:
            try:
                await websocket.receive_text()
            except Exception:
                await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
    except Exception:
        manager.disconnect(websocket, session_id)

class UserTurn(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    text: str = Field(..., min_length=1, max_length=10000)
    is_interruption: bool = False

@app.post("/run")
async def run_event(ev: RunEvent):
    state = await store.ensure(ev.session_id)

    if ev.event == "start":
        state.ended = False
        state.paused = False
        state.agent_order = ev.agents or state.agent_order
        if ev.voices:
            state.voices.update(ev.voices)
        if ev.target_minutes is None or ev.target_minutes == 0:
            state.target_ms = 0
            state.deadline_ts = None
        else:
            state.target_ms = int(ev.target_minutes * 60_000)
            state.deadline_ts = time.time() + (state.target_ms / 1000.0)
        state.audio_ms = 0
        state.ended_reason = None
        state.turn_audio = {}
        state.final_wav = None

        # ✅ Activate live streaming for this session
        stream_manager.activate(ev.session_id)

        # Background broadcaster (mixer-based, optional)
        if not state.audio_broadcast_task or state.audio_broadcast_task.done():
            state.audio_broadcast_task = asyncio.create_task(audio_broadcaster(ev.session_id))

        state.updated_at = time.time()
        await store.upsert(state)
        return {"status": "started", "session_id": ev.session_id, "target_ms": state.target_ms}

    if ev.event == "end":
        await _end_session(state, "manual_end")
        return {"status": "ended", "session_id": ev.session_id}

    if ev.event == "pause":
        state.paused = True
        state.updated_at = time.time()
        await store.upsert(state)
        # Pausing live stream
        stream_manager.deactivate(ev.session_id)
        return {"status": "paused", "session_id": ev.session_id}

    if ev.event == "resume":
        state.paused = False
        state.updated_at = time.time()
        await store.upsert(state)
        stream_manager.activate(ev.session_id)
        return {"status": "resumed", "session_id": ev.session_id}

    if state.ended:
        raise HTTPException(status_code=400, detail="Session ended")

    if ev.max_turns:
        state.max_turns_per_round = max(1, min(ev.max_turns, settings.MAX_TURNS_PER_ROUND))

    if ev.event == "topic_input":
        if not ev.input:
            raise HTTPException(status_code=400, detail="Missing topic")
        state.topic = ev.input.strip()
        state.updated_at = time.time()
        await store.upsert(state)
        try:
            await _round_robin_once(state)
        except Exception:
            await _end_session(state, "error")
            raise HTTPException(status_code=500, detail="Orchestration error")
        return {"status": "ok", "session_id": ev.session_id}

    if ev.event in ("message", "interrupt"):
        if not ev.input:
            raise HTTPException(status_code=400, detail="Missing message")
        turn = Turn(
            turn_id=len(state.history) + 1,
            agent="user",
            role="user",
            response=ev.input.strip(),
            audio_path=None,
            audio_data=b"",
            timestamp=time.time()
        )
        state.history.append(turn)
        if ev.event == "interrupt":
            await _end_session(state, "interrupted_stop")
            return {"status": "ok", "session_id": ev.session_id}
        try:
            await _round_robin_once(state, user_context=ev.input.strip(), is_interrupt=False)
        except Exception:
            await _end_session(state, "error")
            raise HTTPException(status_code=500, detail="Orchestration error")
        return {"status": "ok", "session_id": ev.session_id}

    if ev.event == "stop":
        await _end_session(state, "requested_stop")
        return {"status": "ok", "session_id": ev.session_id}

    raise HTTPException(status_code=400, detail=f"Unknown event: {ev.event}")

@app.post("/conversation/user-turn")
async def user_turn(body: UserTurn):
    st = await store.ensure(body.session_id)
    if st.ended:
        raise HTTPException(status_code=400, detail="Session already ended")
    turn = Turn(
        turn_id=len(st.history) + 1,
        agent="user",
        role="user",
        response=body.text.strip(),
        audio_path=None,
        audio_data=b"",
        timestamp=time.time()
    )
    st.history.append(turn)
    await _round_robin_once(st, user_context=body.text.strip(), is_interrupt=body.is_interruption)
    return {"status": "ok", "session_id": body.session_id, "round": st.current_round}

# ========= Auto-play helpers for frontends =========
def _safe_read_file_bytes(p: str) -> bytes:
    try:
        return Path(p).read_bytes()
    except Exception:
        return b""

@app.post("/session/{session_id}/next-audio")
async def get_next_unplayed_audio(session_id: str):
    """
    Returns the next unplayed turn's audio as base64 (WAV), and marks it as played.
    Response:
      - { "status": "ok", "turn_id": int, "agent": str, "audio_b64": str, "mime": "audio/wav" }
      - { "status": "none" }  if nothing to play yet
    """
    st = await store.get(session_id)
    if not st:
        raise HTTPException(status_code=404, detail="Session not found")

    for tid in sorted(st.turn_audio.keys()):
        meta = st.turn_audio.get(tid) or {}
        if not isinstance(meta, dict):
            continue
        if meta.get("played"):
            continue
        p = meta.get("path")
        if not p or not Path(p).exists():
            # mark as played to skip broken entry
            meta["played"] = True
            continue

        audio_bytes = _safe_read_file_bytes(p)
        meta["played"] = True
        st.updated_at = time.time()
        await store.upsert(st)

        return {
            "status": "ok",
            "turn_id": tid,
            "agent": meta.get("speaker"),
            "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime": "audio/wav",
        }

    return {"status": "none"}

@app.post("/session/{session_id}/reset-playback")
async def reset_playback(session_id: str):
    """
    Marks all turn audios as unplayed. Useful to replay from the beginning.
    """
    st = await store.get(session_id)
    if not st:
        raise HTTPException(status_code=404, detail="Session not found")
    for _, meta in (st.turn_audio or {}).items():
        if isinstance(meta, dict):
            meta["played"] = False
    st.updated_at = time.time()
    await store.upsert(st)
    return {"status": "ok", "reset": True}

@app.get("/sessions")
async def list_sessions():
    return {"sessions": _list_sessions_meta(), "registry_keys": list(REGISTRY.keys())}

@app.post("/registry/reload")
async def reload_registry():
    global REGISTRY
    REGISTRY = _load_registry()
    return {"status": "ok", "count": len(REGISTRY)}

@app.get("/registry")
async def get_registry():
    try:
        return {
            "path": str(Path(settings.REGISTRY_PATH).resolve()),
            "count": len(REGISTRY),
            "agents": REGISTRY,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/session/{session_id}/state")
async def session_state(session_id: str):
    st = await store.get(session_id)
    if not st:
        raise HTTPException(status_code=404, detail="Session not found")
    payload = {
        "session_id": st.session_id,
        "topic": st.topic,
        "created_at": st.created_at,
        "updated_at": st.updated_at,
        "paused": st.paused,
        "ended": st.ended,
        "agent_order": st.agent_order,
        "voices": st.voices,
        "mute": st.mute,
        "current_round": st.current_round,
        "max_turns_per_round": st.max_turns_per_round,
        "history": [t.dict() for t in st.history],
        "audio_ms": st.audio_ms,
        "target_ms": st.target_ms,
        "ended_reason": st.ended_reason,
        "final_wav": st.final_wav,
        "turn_audio": st.turn_audio,
    }
    return JSONResponse(content=_to_json_safe(payload))

@app.get("/conversation/session/{session_id}/state")
async def compat_state_proxy(session_id: str):
    if session_id.startswith("health_check_"):
        return JSONResponse(
            status_code=200,
            content={"status": "health_check", "session_id": session_id, "exists": False, "is_health_check": True}
        )
    st = await store.get(session_id)
    if not st:
        raise HTTPException(
            status_code=404,
            detail="Session not found",
            headers={"X-Session-Status": "not_found"}
        )
    return JSONResponse(content=_to_json_safe(st.dict()))

_last_health_snapshot = {"sessions": 0}
_last_health_log_ts: float = 0.0
HEALTH_DEBOUNCE_SECONDS = int(os.getenv("GRAPH_HEALTH_DEBOUNCE_SECONDS", "30"))

@app.get("/health")
@limiter.limit(settings.HEALTH_CHECK_RATE_LIMIT)
async def health(request: Request, background_tasks: BackgroundTasks):
    global _last_health_snapshot, _last_health_log_ts
    try:
        sessions = list(store.sessions.keys())
        snap = {"sessions": len(sessions)}
        now = time.time()
        should_log = False
        if snap != _last_health_snapshot:
            should_log = True
            _last_health_snapshot = snap
        elif now - _last_health_log_ts >= HEALTH_DEBOUNCE_SECONDS:
            should_log = True
        if should_log:
            logger.info(f"/health snapshot: sessions={snap['sessions']} ts={int(now)}")
            _last_health_log_ts = now
        return {"status": "ok", "sessions": snap["sessions"], "ts": int(now)}
    except Exception:
        return {"status": "error"}

@app.get("/session/{session_id}/mute-status")
async def mute_status(session_id: str, agent: Optional[str] = Query(None)):
    st = await store.get(session_id)
    if not st:
        return {"session_id": session_id, "muted": False}
    muted = st.ended or st.paused or (agent and st.mute.get(agent, False))
    return {"session_id": session_id, "muted": muted}

@app.post("/session/{session_id}/mute")
async def mute_agent(session_id: str, agent: str = Query(...)):
    if agent not in REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")
    st = await store.ensure(session_id)
    st.mute[agent] = True
    st.updated_at = time.time()
    await store.upsert(st)
    return {"status": "muted", "session_id": session_id, "agent": agent}

@app.post("/session/{session_id}/unmute")
async def unmute_agent(session_id: str, agent: str = Query(...)):
    if agent not in REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")
    st = await store.ensure(session_id)
    st.mute[agent] = False
    st.updated_at = time.time()
    await store.upsert(st)
    return {"status": "unmuted", "session_id": session_id, "agent": agent}

@app.post("/session/{session_id}/voices")
async def set_voice(session_id: str, agent: str = Query(...),
                    voice: str = Query(..., min_length=3, max_length=80)):
    if agent not in REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")
    st = await store.ensure(session_id)
    st.voices[agent] = voice
    st.updated_at = time.time()
    await store.upsert(st)
    return {"status": "ok", "session_id": session_id, "agent": agent, "voice": voice}

@app.post("/session/{session_id}/agents")
async def set_agents(session_id: str, agents: List[str] = Body(...)):
    st = await store.ensure(session_id)
    valid_agents = [a for a in agents if a in REGISTRY]
    st.agent_order = valid_agents or list(settings.DEFAULT_AGENTS)
    st.updated_at = time.time()
    await store.upsert(st)
    return {"status": "ok", "session_id": session_id, "agents": st.agent_order}

@app.post("/session/{session_id}/export")
async def export_session(session_id: str, format: str = Query("html")):
    st = await store.get(session_id)
    if not st:
        raise HTTPException(status_code=404, detail="Session not found")

    ordered = [st.turn_audio[k] for k in sorted(st.turn_audio.keys()) if isinstance(st.turn_audio.get(k), dict)]
    clip_paths = [d.get("path") for d in ordered if d.get("path")]
    final_dir = Path(settings.FINAL_AUDIO_DIR)
    try: final_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e: logger.warning(f"could not create FINAL_AUDIO_DIR={final_dir}: {e}")
    try:
        logger.info(f"export_begin session_id={session_id} clips={len(clip_paths)} out_dir={final_dir}")
    except Exception:
        pass
    out_file = str(final_dir / f"final_{session_id}.wav")
    stitched = _stitch_wavs(clip_paths, out_file) if clip_paths else None
    if stitched:
        st.final_wav = stitched
        st.updated_at = time.time()
        await store.upsert(st)
        try:
            size = Path(stitched).stat().st_size if Path(stitched).exists() else 0
            logger.info(f"export_done session_id={session_id} file={stitched} bytes={size} clips={len(clip_paths)}")
        except Exception:
            pass
    else:
        try:
            logger.info(f"export_no_audio session_id={session_id} clips={len(clip_paths)}")
        except Exception:
            pass
        return {"status": "no_audio", "message": "No audio clips to stitch"}

    agent4 = REGISTRY.get("agent4", {})
    base = str(agent4.get("url","")).rstrip("/")
    if not base:
        raise HTTPException(status_code=400, detail="Agent4 not registered")

    content = {
        "conversation_history": [
            {
                "turn_id": t.turn_id,
                "speaker": t.agent,
                "message": {
                    "content": t.response,
                    "timestamp": datetime.utcfromtimestamp(t.timestamp).isoformat() + "Z",
                    "audio_path": t.audio_path
                }
            } for t in st.history
        ]
    }
    req = {
        "format": format,
        "title": f"Podcast_{session_id}",
        "content": content,
        "metadata": {"topic": st.topic, "agents": st.agent_order, "target_ms": st.target_ms, "audio_ms": st.audio_ms, "ended_reason": st.ended_reason, "final_wav": st.final_wav},
        "include_audio_links": True,
        "include_timestamps": True,
        "session_id": session_id
    }
    return await _post_agent4_export(base, req)

@retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _post_agent4_export(base_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT) as c:
        r = await c.post(f"{base_url}/export", json=payload)
        if r.status_code >= 500:
            raise RuntimeError(f"agent4 5xx: {r.status_code}")
        if r.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Agent4 export failed: {r.text}")
        return r.json()

@app.get("/metrics")
async def metrics():
    return {
        "sessions": {
            "total": len(store.sessions),
            "active": sum(1 for s in store.sessions.values() if not s.ended),
            "health_checks": sum(1 for sid in store.sessions if sid.startswith("health_check_"))
        },
        "connections": {
            "websocket": sum(len(v) for v in manager.active_connections.values()),
            "audio_streams": len(stream_manager.active_streams)
        },
        "system": {
            "registry_agents": len(REGISTRY),
            "uptime": time.time() - app.start_time if hasattr(app, "start_time") else 0
        }
    }

async def _cleanup_health_sessions():
    try:
        for sid in list(store.sessions.keys()):
            if sid.startswith("health_check_"):
                del store.sessions[sid]
                path = _session_path(sid)
                if path.exists():
                    path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Health session cleanup failed: {e}")

@app.get("/session/{session_id}/final-audio")
async def get_final_audio(session_id: str):
    st = await store.get(session_id)
    if not st:
        raise HTTPException(status_code=404, detail="Session not found")

    if (not st.final_wav or not Path(st.final_wav).exists()):
        if st.ended:
            try:
                await _finalize_audio(st)
            except Exception:
                pass
        if not st.final_wav or not Path(st.final_wav).exists():
            return {"status": "pending", "audio_path": None}

    return {"status": "ready", "audio_path": st.final_wav}

@app.get("/session/{session_id}/final-audio/file")
async def get_final_audio_file(session_id: str):
    st = await store.get(session_id)
    if not st or not st.final_wav or not Path(st.final_wav).exists():
        raise HTTPException(status_code=404, detail="Final audio not available")
    return FileResponse(st.final_wav, media_type="audio/wav", filename=f"{session_id}.wav")

@app.get("/")
async def root():
    return RedirectResponse(url="/docs")

# ---- Debug helpers ----
from typing import List as _List

def _present(k: str) -> bool:
    try:
        v = os.getenv(k, "")
        return bool(isinstance(v, str) and v.strip())
    except Exception:
        return False

def _looks_url(k: str) -> bool:
    try:
        v = os.getenv(k, "")
        return isinstance(v, str) and (v.startswith("http://") or v.startswith("https://"))
    except Exception:
        return False

def attach_debug_config(app: FastAPI, service_name: str, required_vars: _List[str], url_vars: _List[str] = []):
    @app.get("/debug/config")
    def debug_config():
        try:
            ok: Dict[str, str] = {}
            for k in required_vars:
                ok[k] = "present" if _present(k) else "MISSING"
            for k in url_vars:
                ok[f"{k}_is_url"] = "ok" if _looks_url(k) else "INVALID_URL"
            return {"service": service_name, "vars": ok}
        except Exception as e:
            return {"service": service_name, "error": f"{type(e).__name__}: {e}"}

    @app.get("/debug/effective")
    def debug_effective():
        try:
            reg_path = str(Path(settings.REGISTRY_PATH).resolve())
        except Exception:
            reg_path = settings.REGISTRY_PATH
        return {
            "service": service_name,
            "settings": {
                "AGENT1_URL": settings.AGENT1_URL,
                "AGENT2_URL": settings.AGENT2_URL,
                "AGENT3_URL": settings.AGENT3_URL,
                "AGENT4_URL": settings.AGENT4_URL,
                "AGENT5_URL": settings.AGENT5_URL,
                "FINAL_AUDIO_DIR": settings.FINAL_AUDIO_DIR,
                "REGISTRY_PATH": reg_path,
            },
            "registry": {
                "path": reg_path,
                "count": len(REGISTRY),
                "agents": REGISTRY,
            }
        }

# Startup
@app.on_event("startup")
async def startup():
    app.start_time = time.time()
    logger.info("Hybrid Podcast Orchestrator starting up")
    logger.info(f"State directory: {STATE_DIR.absolute()}")
    logger.info(f"Registered agents: {len(REGISTRY)}")

# Attach debug config endpoint
attach_debug_config(
    app,
    "graph",
    required_vars=["AGENT1_URL","AGENT2_URL","AGENT3_URL","AGENT4_URL","AGENT5_URL"],
    url_vars=["AGENT1_URL","AGENT2_URL","AGENT3_URL","AGENT4_URL","AGENT5_URL"]
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "graph:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level="warning",
        access_log=False
    )
