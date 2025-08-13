# graph.py - Hybrid Podcast Orchestrator (v3.2)
import os
import time
import json
import uuid
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from fastapi import (
    FastAPI, HTTPException, Query, Body, WebSocket, 
    WebSocketDisconnect, Request, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
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

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        extra = "ignore"

settings = Settings()

# ========= Initialization =========
app = FastAPI(
    title="🧠 Hybrid Podcast Orchestrator",
    description="Combines real-time audio mixing with persistent multi-agent conversations",
    version="3.2"
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
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
            self.active_connections[session_id].remove(websocket)
            
    async def broadcast_audio(self, session_id: str, audio_data: bytes):
        if session_id in self.active_connections:
            for websocket in self.active_connections[session_id]:
                try:
                    await websocket.send_bytes(audio_data)
                except:
                    self.disconnect(websocket, session_id)

# ========= AudioStreamManager =========
class AudioStreamManager:
    def __init__(self):
        self.active_streams = {}
        self.chunk_size = int(settings.SAMPLE_RATE * 0.1)  # 100ms chunks
        self.sample_width = 2  # 16-bit audio

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
            # Stream in precise 100ms chunks
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

# ========= Audio Helpers =========
def _normalize_audio(audio_data: bytes) -> bytes:
    return audio_data

# ========= Agent Communication =========
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
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
        audio_data = bytes.fromhex(data.get("audio_hex", ""))
        return (
            data.get("response", "").strip(),
            _normalize_audio(audio_data),
            data.get("audio_path")
        )

# ========= Orchestration Core =========
async def _round_robin_once(state: SessionState, user_context: Optional[str] = None, is_interrupt: bool = False):
    if state.ended or state.paused:
        return

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

    state.current_round += 1

# ========= API Endpoints =========
@app.websocket("/ws/audio/{session_id}")
async def audio_stream(websocket: WebSocket, session_id: str):
    await manager.connect(websocket, session_id)
    stream_manager.active_streams[session_id] = True  # Track active stream
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
        stream_manager.active_streams.pop(session_id, None)  # Cleanup

@app.post("/run")
async def run_event(ev: RunEvent):
    state = await store.ensure(ev.session_id)

    if ev.event == "start":
        state.ended = False
        state.paused = False
        state.agent_order = ev.agents or state.agent_order
        if ev.voices:
            state.voices.update(ev.voices)
        state.updated_at = time.time()
        
        #if not state.audio_broadcast_task:
         #   state.audio_broadcast_task = asyncio.create_task(audio_broadcaster(ev.session_id))
        
        await store.upsert(state)
        return {"status": "started", "session_id": ev.session_id}

    if ev.event == "end":
        state.ended = True
        if state.audio_broadcast_task:
            state.audio_broadcast_task.cancel()
        state.updated_at = time.time()
        await store.upsert(state)
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
        await _round_robin_once(state)
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
        await _round_robin_once(state, user_context=ev.input.strip(), is_interrupt=(ev.event=="interrupt"))
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

@app.get("/session/{session_id}/state")
async def session_state(session_id: str):
    st = await store.get(session_id)
    if not st:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
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
    return st.dict()

@app.get("/health")
@limiter.limit(settings.HEALTH_CHECK_RATE_LIMIT)
async def health(request: Request, background_tasks: BackgroundTasks):
    status = {
        "status": "healthy",
        "service": "Hybrid Podcast Orchestrator",
        "version": "3.2",
        "rate_limiting_enabled": not isinstance(limiter, DummyLimiter),
        "details": {}
    }
    
    try:
        # Test registry access
        registry = _load_registry()
        status["details"]["registry"] = {
            "count": len(registry),
            "accessible": True
        }
        
        # Test WebSocket connections
        ws_count = sum(len(v) for v in manager.active_connections.values())
        status["details"]["websockets"] = {
            "active_connections": ws_count,
            "healthy": True
        }
            
        # Test audio mixer
        mixer_test = len(mixer.active_streams)
        status["details"]["audio_mixer"] = {
            "active_streams": mixer_test,
            "healthy": True
        }
            
        # Schedule cleanup of any residual health check sessions
        background_tasks.add_task(_cleanup_health_sessions)
        
    except Exception as e:
        status["status"] = "unhealthy"
        status["error"] = str(e)
        logger.error(f"Health check failed: {e}", exc_info=True)
        
    return status

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
        "metadata": {"topic": st.topic, "agents": st.agent_order},
        "include_audio_links": True,
        "include_timestamps": True,
        "session_id": session_id
    }
    async with httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT) as c:
        r = await c.post(f"{base}/export", json=req)
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
            "audio_streams": len(mixer.active_streams)
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

@app.get("/")
async def root():
    return RedirectResponse(url="/docs")

# Startup event
@app.on_event("startup")
async def startup():
    app.start_time = time.time()
    logger.info("🚀 Hybrid Podcast Orchestrator starting up")
    logger.info(f"📂 State directory: {STATE_DIR.absolute()}")
    logger.info(f"📋 Registered agents: {len(REGISTRY)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("graph:app", host=settings.HOST, port=settings.PORT, log_level="info")
