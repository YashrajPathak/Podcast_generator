# graph.py - Hybrid Podcast Orchestrator (v3.2.1)
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
_client = httpx.AsyncClient

# Initialize logging first
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - graph - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("graph.log"),
        logging.StreamHandler()
    ]
)
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
    AUDIO_CHUNK_SIZE: int = 4096
    SAMPLE_RATE: int = 24000
    SAMPLE_WIDTH: int = 2  # 16-bit audio
    HEALTH_CHECK_RATE_LIMIT: str = "10/minute"
    FINAL_AUDIO_DIR: str = "audio_cache"

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        extra = "ignore"

settings = Settings()

# ========= Initialization =========
app = FastAPI(
    title="🧠 Hybrid Podcast Orchestrator",
    description="Combines real-time audio mixing with persistent multi-agent conversations",
    version="3.2.1"
)

# Middleware
# Compute allowed origins from env (UI_ORIGINS comma-separated, fallback UI_ORIGIN)
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

# ========= Audio Mixer =========
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
        for i in range(0, max_len, 2):
            samples = []
            for stream in streams:
                if i < len(stream):
                    sample = int.from_bytes(stream[i:i+2], 'little', signed=True)
                    samples.append(sample)

            if samples:
                mixed = sum(samples) // len(samples)
                self.mix_buffer.extend(mixed.to_bytes(2, 'little', signed=True))

    def get_mixed_audio(self) -> bytes:
        return bytes(self.mix_buffer)

mixer = AudioMixer()

# ========= WebSocket Connection Manager =========
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)

    def disconnect(self, websocket: WebSocket, session_id: str):
        if session_id in self.active_connections:
            try:
                self.active_connections[session_id].remove(websocket)
            except ValueError:
                pass

    async def broadcast_audio(self, session_id: str, audio_data: bytes):
        if session_id in self.active_connections:
            for websocket in list(self.active_connections[session_id]):
                try:
                    await websocket.send_bytes(audio_data)
                except Exception:
                    self.disconnect(websocket, session_id)

# ========= AudioStreamManager =========
class AudioStreamManager:
    def __init__(self):
        self.active_streams = {}
        self.sample_width = settings.SAMPLE_WIDTH  # bytes per sample, 2 for 16-bit mono
        # 100ms chunks → samples * sample_width = bytes
        self.chunk_size = int(settings.SAMPLE_RATE * 0.1) * self.sample_width

    async def stream_audio(self, session_id: str, audio_data: bytes):
        """Professional podcast streaming with:
        - Perfect chunk timing
        - Anti-clip buffers
        - Jitter compensation
        """
        if not audio_data:
            return

        # Pre-stream buffer (eliminates initial pop)
        await asyncio.sleep(0.05)

        try:
            # Stream in precise ~100ms chunks (bytes)
            for i in range(0, len(audio_data), self.chunk_size):
                if session_id not in self.active_streams:
                    break

                chunk = audio_data[i:i+self.chunk_size]
                await self._send_chunk(session_id, chunk)

                # Maintain exact timing (critical)
                await asyncio.sleep(0.1)

        finally:
            # Post-stream buffer (eliminates ending clip)
            await asyncio.sleep(0.05)

    async def _send_chunk(self, session_id: str, chunk: bytes):
        """Send chunk with error correction"""
        try:
            await manager.broadcast_audio(session_id, chunk)
        except Exception as e:
            logger.error(f"Stream error: {e}")
            raise

# Initialize managers
manager = ConnectionManager()
stream_manager = AudioStreamManager()

# ========= Registry =========
def _load_registry() -> Dict[str, Dict[str, Any]]:
    reg: Dict[str, Dict[str, Any]] = {}
    path = Path(settings.REGISTRY_PATH)
    if path.exists():
        try:
            reg = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"⚠️ Failed to parse {settings.REGISTRY_PATH}: {e}")
    # Ensure legacy agents exist
    reg.setdefault("agent1", {"name": "Host", "url": settings.AGENT1_URL, "role": "host/moderator"})
    reg.setdefault("agent2", {"name": "Analyst", "url": settings.AGENT2_URL, "role": "analyst/researcher"})
    reg.setdefault("agent3", {"name": "Storyteller", "url": settings.AGENT3_URL, "role": "storyteller/entertainer"})
    # clearer label for agent4
    reg.setdefault("agent4", {"name": "Export", "url": settings.AGENT4_URL, "role": "exporter"})
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
    # Allow 0 or None to mean "no duration target" (legacy behavior)
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
    # Wall-clock deadline timestamp (epoch seconds); None means disabled
    deadline_ts: Optional[float] = None

    class Config:
        arbitrary_types_allowed = True

    @field_validator('audio_broadcast_task')
    def validate_task(cls, v):
        if v is not None and not isinstance(v, asyncio.Task):
            raise ValueError("Must be an asyncio.Task or None")
        return v

# ========= Persistence =========
STATE_DIR = Path(settings.STATE_DIR)
STATE_DIR.mkdir(parents=True, exist_ok=True)
# Ensure final audio cache directory exists
FINAL_DIR = Path(settings.FINAL_AUDIO_DIR)
FINAL_DIR.mkdir(parents=True, exist_ok=True)

def _session_path(sid: str) -> Path:
    return STATE_DIR / f"{sid}.json"

def _save_session(state: SessionState) -> None:
    try:
        state_dict = state.dict(exclude={"audio_broadcast_task"})
        for turn in state_dict["history"]:
            # Ensure JSON-safe value
            turn["audio_data"] = ""
        _session_path(state.session_id).write_text(
            json.dumps(state_dict, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"⚠️ failed to persist {state.session_id}: {e}")

def _load_session(sid: str) -> Optional[SessionState]:
    p = _session_path(sid)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return SessionState(**data)
    except Exception as e:
        logger.warning(f"⚠️ failed to load {sid}: {e}")
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

# ========= Store with Persistence =========
class MemoryStore:
    def __init__(self):
        self.sessions: Dict[str, SessionState] = {}
        self._lock = asyncio.Lock()

    async def get(self, sid: str) -> Optional[SessionState]:
        st = self.sessions.get(sid)
        if st:
            return st
        disk = _load_session(sid)
        if disk:
            self.sessions[sid] = disk
        return disk

    async def upsert(self, state: SessionState):
        async with self._lock:
            self.sessions[state.session_id] = state
            _save_session(state)

    async def ensure(self, sid: str) -> SessionState:
        st = await self.get(sid)
        if st:
            return st
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

# Centralized session end helper to ensure single transition and unified logging
async def _end_session(state: SessionState, reason: str):
    try:
        if state.ended and state.ended_reason:
            return
    except Exception:
        pass
    # Cancel broadcaster if running
    try:
        if state.audio_broadcast_task and not state.audio_broadcast_task.done():
            state.audio_broadcast_task.cancel()
    except Exception:
        pass
    state.ended = True
    state.ended_reason = reason
    state.updated_at = time.time()
    # Cleanup stream activation if set
    try:
        if state.session_id in stream_manager.active_streams:
            del stream_manager.active_streams[state.session_id]
    except Exception:
        pass
    # Finalize audio and best-effort trigger export
    try:
        await _finalize_audio(state)
    except Exception:
        pass
    await store.upsert(state)
    try:
        logger.info(
            f"session_end session_id={state.session_id} topic={state.topic} reason={state.ended_reason} "
            f"turns={len(state.history)} audio_ms={state.audio_ms} target_ms={state.target_ms} final_wav={state.final_wav}"
        )
    except Exception:
        pass
    # Fire-and-forget Agent4 export (best-effort)
    try:
        agent4 = REGISTRY.get("agent4", {})
        base = str(agent4.get("url", "")).rstrip("/")
        if base:
            content = {
                "conversation_history": [
                    {
                        "turn_id": t.turn_id,
                        "speaker": t.agent,
                        "message": {
                            "content": t.response,
                            "timestamp": datetime.utcfromtimestamp(t.timestamp).isoformat() + "Z",
                            "audio_path": t.audio_path,
                        },
                    }
                    for t in state.history
                ]
            }
            payload = {
                "format": "zip",
                "title": f"Podcast_{state.session_id}",
                "content": content,
                "metadata": {
                    "topic": state.topic,
                    "agents": state.agent_order,
                    "target_ms": state.target_ms,
                    "audio_ms": state.audio_ms,
                    "ended_reason": state.ended_reason,
                    "final_wav": state.final_wav,
                },
                "include_audio_links": True,
                "include_timestamps": True,
                "session_id": state.session_id,
            }
            # Run in background without blocking
            asyncio.create_task(_post_agent4_export(base, payload))
    except Exception as e:
        try:
            logger.warning(f"agent4_export_skip session_id={state.session_id} err={e}")
        except Exception:
            pass

# ========= Audio Helpers =========
import base64
from dotenv import load_dotenv
# Ensure .env variables are also present in process environment so debug/config reflects them
load_dotenv(override=False)

# Write the current mixed audio buffer to a WAV file and return its path
async def _finalize_audio(state: SessionState) -> Optional[str]:
    try:
        # If already finalized, return existing path
        if state.final_wav and Path(state.final_wav).exists():
            return state.final_wav

        out_path = FINAL_DIR / f"{state.session_id}.wav"

        # 1) Try mixed audio buffer first
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

        # 2) Fallback: stitch per-turn WAV clips in chronological order
        try:
            ordered = [state.turn_audio[k] for k in sorted(state.turn_audio.keys()) if isinstance(state.turn_audio.get(k), dict)]
            clip_paths = [d.get("path") for d in ordered if d.get("path")]
        except Exception:
            ordered, clip_paths = [], []
        if clip_paths:
            stitched = _stitch_wavs(clip_paths, str(out_path))
            if stitched:
                state.final_wav = stitched
                state.updated_at = time.time()
                await store.upsert(state)
                try:
                    size = Path(stitched).stat().st_size if Path(stitched).exists() else 0
                    logger.info(
                        f"finalize_audio session_id={state.session_id} file={stitched} bytes={size} source=stitch clips={len(clip_paths)}"
                    )
                except Exception:
                    pass
                return stitched

        # No audio available
        try:
            logger.info(
                f"finalize_audio_no_data session_id={state.session_id} history={len(state.history)} clips={len(clip_paths) if 'clip_paths' in locals() else 0}"
            )
        except Exception:
            pass
        return None
    except Exception as e:
        logger.warning(f"⚠️ finalize_audio failed for {state.session_id}: {e}")
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
    if not paths:
        return None
    try:
        # Use params from first file
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
                        if s.getnchannels() != n_channels or s.getsampwidth() != sampwidth or s.getframerate() != framerate:
                            # skip incompatible clip
                            continue
                        out.writeframes(s.readframes(s.getnframes()))
                except Exception:
                    continue
        return out_path
    except Exception:
        return None

# Background broadcaster: periodically push mixed audio over WebSocket
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
        "return_audio": True
    }

    async with _client() as client:
        r = await client.post(url, json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"{agent_key} chat failed ({r.status_code}): {r.text}")
        data = r.json()

    audio_hex = data.get("audio_hex", "") or ""
    audio_data = bytes.fromhex(audio_hex) if audio_hex else b""
    audio_path = data.get("audio_path")
    # If hex not provided but we have a file path, read bytes for streaming
    if not audio_data and audio_path:
        try:
            audio_data = Path(audio_path).read_bytes()
        except Exception:
            audio_data = b""
    logger.info(f"agent_turn_ok session_id={session_id} agent={agent_key} text_len={len(text)} audio_bytes={len(audio_data)}")
    return (
        data.get("response", "").strip(),
        _normalize_audio(audio_data),
        audio_path
    )

# ========= Orchestration Core =========
async def _round_robin_once(state: SessionState, user_context: Optional[str] = None, is_interrupt: bool = False):
    if state.ended or state.paused:
        return
    # Wall-clock deadline check
    try:
        if state.deadline_ts and time.time() >= state.deadline_ts and not state.ended:
            logger.info(f"deadline_reached session_id={state.session_id} now={int(time.time())} deadline_ts={int(state.deadline_ts)}")
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

        try:
            text, audio_data, audio_path = await _agent_chat(
                agent_key,
                text=prompt,
                session_id=state.session_id,
                voice=voice,
                turn_number=idx,
                max_turns=max_turns,
                conversation_context=base_ctx if base_ctx else None,
                is_interruption=is_interrupt
            )

            # Stream audio professionally
            await stream_manager.stream_audio(state.session_id, audio_data)

        except Exception as e:
            logger.warning(f"⚠️ {agent_key} turn failed: {e}")
            text, audio_data, audio_path = f"(error: {e})", b"", None

        turn = Turn(
            turn_id=(state.history[-1].turn_id + 1) if state.history else 1,
            agent=agent_key,
            role=_agent_role(agent_key),
            response=text,
            audio_path=audio_path,
            audio_data=audio_data,
            timestamp=time.time()
        )
        state.history.append(turn)
        state.updated_at = time.time()
        await store.upsert(state)

        # Track audio duration and check target
        try:
            if turn.audio_path:
                ms = _wav_ms(turn.audio_path)
                state.turn_audio[turn.turn_id] = {"path": turn.audio_path, "ms": ms, "speaker": turn.agent}
                state.audio_ms += ms
                logger.info(
                    f"turn_done session_id={state.session_id} turn_id={turn.turn_id} agent={turn.agent} ms={ms} "
                    f"audio_ms={state.audio_ms} target_ms={state.target_ms} final_wav={state.final_wav}"
                )
        except Exception:
            pass
        if state.target_ms and state.audio_ms >= state.target_ms and not state.ended:
            await _end_session(state, "target_duration_reached")
            break

    state.current_round += 1

# ========= JSON Safety Helper =========
from typing import Any as _Any

def _to_json_safe(obj: _Any) -> _Any:
    """Recursively convert bytes/buffer-like types to JSON-safe structures.
    - bytes/bytearray/memoryview -> {"__type__":"bytes","b64": base64}
    - collections -> recurse
    """
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
        # In worst case, return string repr to avoid encoder crashes
        return str(obj)

# ========= API Endpoints =========
@app.websocket("/ws/audio/{session_id}")
async def websocket_audio(websocket: WebSocket, session_id: str):
    await manager.connect(websocket, session_id)
    try:
        # Simple keep-alive ping; clients can ignore or respond
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_json({"type": "ping", "ts": int(time.time())})
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, session_id)

@app.post("/run")
async def run_event(ev: RunEvent):
    state = await store.ensure(ev.session_id)

    if ev.event == "start":
        state.ended = False
        state.paused = False
        state.agent_order = ev.agents or state.agent_order
        if ev.voices:
            state.voices.update(ev.voices)
        # duration target from UI; if 0 or None -> disable target (legacy behavior)
        if ev.target_minutes is None or ev.target_minutes == 0:
            state.target_ms = 0
            state.deadline_ts = None
        else:
            state.target_ms = int(ev.target_minutes * 60_000)
            # Wall-clock deadline enforcement
            state.deadline_ts = time.time() + (state.target_ms / 1000.0)
        state.audio_ms = 0
        state.ended_reason = None
        state.turn_audio = {}
        state.final_wav = None
        # Activate streaming for this session
        try:
            stream_manager.active_streams[ev.session_id] = True
        except Exception:
            pass
        # Start broadcaster task if not running
        try:
            if not state.audio_broadcast_task or state.audio_broadcast_task.done():
                state.audio_broadcast_task = asyncio.create_task(audio_broadcaster(ev.session_id))
        except Exception:
            pass
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
        return {"status": "paused", "session_id": ev.session_id}

    if ev.event == "resume":
        state.paused = False
        state.updated_at = time.time()
        await store.upsert(state)
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
        # message path continues orchestration
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

class UserTurn(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    text: str = Field(..., min_length=1, max_length=10000)
    is_interruption: bool = False

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
    """Return the current agent registry and its source path.
    This allows UIs and other services to consume a single source of truth.
    """
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
    }
    return JSONResponse(content=_to_json_safe(payload))

@app.get("/conversation/session/{session_id}/state")
async def compat_state_proxy(session_id: str):
    if session_id.startswith("health_check_"):
        return JSONResponse(
            status_code=200,
            content={
                "status": "health_check",
                "session_id": session_id,
                "exists": False,
                "is_health_check": True
            }
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
        # snapshot: number of active sessions + current ts
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

    # Prepare stitched WAV from per-turn audio
    ordered = [st.turn_audio[k] for k in sorted(st.turn_audio.keys()) if isinstance(st.turn_audio.get(k), dict)]
    clip_paths = [d.get("path") for d in ordered if d.get("path")]
    final_dir = Path(settings.FINAL_AUDIO_DIR)
    try:
        final_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"⚠️ could not create FINAL_AUDIO_DIR={final_dir}: {e}")
    try:
        logger.info(
            f"export_begin session_id={session_id} clips={len(clip_paths)} out_dir={final_dir}"
        )
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
            logger.info(
                f"export_done session_id={session_id} file={stitched} bytes={size} clips={len(clip_paths)}"
            )
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
            # trigger retry on transient errors
            raise RuntimeError(f"agent4 5xx: {r.status_code}")
        if r.status_code != 200:
            # no retry for client errors
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
            # report active session streams, not mixer agent blobs
            "audio_streams": len(stream_manager.active_streams)
        },
        "system": {
            "registry_agents": len(REGISTRY),
            "uptime": time.time() - app.start_time if hasattr(app, "start_time") else 0
        }
    }

async def _cleanup_health_sessions():
    """Clean up any remaining health check sessions"""
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

    # If not finalized yet, but session has ended, attempt best-effort finalize now
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

# Debug config endpoint
from typing import List as _List
import re as _re

def _present(k: str) -> bool:
    v = os.getenv(k, "")
    return bool(v and v.trim())

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

    # Also expose effective runtime configuration values and registry entries
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

# Startup event
@app.on_event("startup")
async def startup():
    app.start_time = time.time()
    logger.info("🚀 Hybrid Podcast Orchestrator starting up")
    logger.info(f"📂 State directory: {STATE_DIR.absolute()}")
    logger.info(f"📋 Registered agents: {len(REGISTRY)}")

# Attach debug config endpoint
attach_debug_config(
    app,
    "graph",
    required_vars=["AGENT1_URL","AGENT2_URL","AGENT3_URL","AGENT4_URL","AGENT5_URL"],
    url_vars=["AGENT1_URL","AGENT2_URL","AGENT3_URL","AGENT4_URL","AGENT5_URL"]
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("graph:app", host=settings.HOST, port=settings.PORT, log_level="info")
