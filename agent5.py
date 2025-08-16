# agent5.py – 📊 Logger & Orchestrator + Graph/Export Glue + Ingest (v2.4.1)

import os
import re
import json
import asyncio
import logging
import time
import sqlite3
from typing import Dict, Any, Optional, List, Callable, Tuple, Union
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from datetime import datetime, timezone
import uuid
from pathlib import Path
import threading
import functools

import anyio
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

load_dotenv()  # DEBUG: ensure .env is loaded even if CWD differs

# ---------- Utilities (optional imports you already have in utils.py) ----------
try:
    from utils import (
        settings as util_settings,
        logger as util_logger,
        clean_text_for_processing,
        create_error_response
    )
except Exception:
    util_settings = None
    util_logger = logging.getLogger("Agent5.utils")
    def clean_text_for_processing(x: str, n: int = 10000) -> str:
        x = " ".join((x or "").split())
        return x[:n] + ("..." if len(x) > n else "")
    def create_error_response(msg: str) -> Dict[str, Any]:
        return {"status": "error", "message": msg}

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
def _valid_session_id(s: Optional[str]) -> bool:
    return bool(s and SESSION_ID_RE.match(s))

# ========== Settings ==========
class Settings(BaseSettings):
    # SQLite log store
    DATABASE_PATH: str = "podcast_sessions.db"
    LOG_RETENTION_DAYS: int = 30
    CLEANUP_INTERVAL: int = 3600          # 1 hour
    MAX_LOG_ENTRIES_RETURNED: int = 500   # safety cap for polling
    HEALTH_LOG_DEBOUNCE_SECONDS: int = 30

    # Azure OpenAI (for /v1/chat helper)
    AZURE_OPENAI_KEY: Optional[str] = None
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4o"
    OPENAI_API_VERSION: str = "2024-05-01-preview"

    # Service URLs
    GRAPH_URL: str = "http://localhost:8008"
    AGENT4_URL: str = "http://localhost:8006"

    # Generic agent behavior
    AGENT_TIMEOUT: float = 30.0
    MAX_RETRIES: int = 3

    # Graph ingest worker
    GRAPH_INGEST_ENABLED: bool = False
    GRAPH_POLL_INTERVAL_SEC: float = 10.0

    class Config:
        env_file = ".env"
        extra = "ignore"

try:
    settings = Settings()
except Exception as e:
    print(f"❌ Agent5 Configuration Error: {e}")
    raise

# ========== Logging ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("Agent5")

# Ensure DB directory exists (if a directory component is provided)
try:
    db_parent = Path(settings.DATABASE_PATH).parent
    if str(db_parent) and str(db_parent) not in (".", ""):
        db_parent.mkdir(parents=True, exist_ok=True)
except Exception as e:
    logger.warning(f"⚠️ could not create DB parent dir for {settings.DATABASE_PATH}: {e}")

# Retention worker: periodically purge old rows
async def _retention_worker():
    # DEBUG: periodic retention cleanup
    import sqlite3, time as _t
    while True:
        try:
            cutoff = _t.time() - float(settings.LOG_RETENTION_DAYS) * 86400.0
            deleted_total = 0
            try:
                conn = sqlite3.connect(settings.DATABASE_PATH)
                cur = conn.cursor()
                for sql, param in [
                    ("DELETE FROM events WHERE timestamp < ?", cutoff),
                    ("DELETE FROM sessions WHERE last_activity < ?", cutoff),
                ]:
                    try:
                        cur.execute(sql, (param,))
                        deleted_total += cur.rowcount if (cur.rowcount or 0) > 0 else 0
                    except Exception:
                        pass
                try:
                    conn.commit()
                except Exception:
                    pass
                if deleted_total > 0:
                    logger.info(f"retention_deleted_rows N={deleted_total}")
                    try:
                        cur.execute("VACUUM")
                        conn.commit()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Retention worker DB error: {e}")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            # never crash the worker
            pass
        try:
            await asyncio.sleep(float(settings.CLEANUP_INTERVAL))
        except Exception:
            await asyncio.sleep(3600)

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.AGENT_TIMEOUT)

# ========== Errors & Decorators ==========
class AgentError(Exception):
    """Unified error type surfaced by decorator-wrapped functions."""

SENSITIVE_KEYS = {
    "key", "api_key", "token", "secret", "password", "authorization", "cookie", "set-cookie", "access_token",
    "refresh_token", "client_secret", "bearer"
}

def _redact(obj):
    try:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if any(s in str(k).lower() for s in SENSITIVE_KEYS):
                    out[k] = "***"
                else:
                    out[k] = _redact(v)
            return out
        if isinstance(obj, (list, tuple)):
            return [_redact(v) for v in obj]
        return obj
    except Exception:
        return "***"

def _extract_session_id(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Optional[str]:
    if "session_id" in kwargs and isinstance(kwargs["session_id"], str):
        return kwargs["session_id"]
    for a in args:
        if isinstance(a, str) and 1 <= len(a) <= 100:
            return a
        if hasattr(a, "session_id"):
            return getattr(a, "session_id", None)
    return None

def log_execution(agent5: "Agent5", event_name: Optional[str] = None) -> Callable:
    """Decorator to auto-log start/complete/error to Agent5 (available to wrap your own funcs)."""
    def decorator(func: Callable):
        is_coro = asyncio.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            session_id = _extract_session_id(args, kwargs) or "default"
            name = event_name or func.__name__
            start_ts = time.time()
            await agent5.log_event(SessionEvent(
                session_id=session_id,
                event_type=f"{name}_start",
                content={"args": _redact(args), "kwargs": _redact(kwargs)},
                agent="agent5"
            ))
            try:
                result = await func(*args, **kwargs) if is_coro else await anyio.to_thread.run_sync(func, *args, **kwargs)
                await agent5.log_event(SessionEvent(
                    session_id=session_id,
                    event_type=f"{name}_complete",
                    content={"duration": round(time.time() - start_ts, 3)},
                    agent="agent5"
                ))
                return result
            except Exception as e:
                await agent5.log_event(SessionEvent(
                    session_id=session_id,
                    event_type=f"{name}_error",
                    content={"error": str(e)},
                    agent="agent5"
                ))
                raise AgentError(f"Failed in {name}: {e}") from e

        if not is_coro:
            async def adapter(*args, **kwargs):
                return await async_wrapper(*args, **kwargs)
            return adapter
        return async_wrapper
    return decorator

# ========== Models ==========
class SessionEvent(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    event_type: str = Field(..., min_length=1, max_length=64)
    content: Dict[str, Any] = Field(default_factory=dict)
    agent: str = Field(default="agent5")
    timestamp: float = Field(default_factory=time.time)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class SessionInfo(BaseModel):
    session_id: str
    created_at: float
    last_activity: float
    total_events: int
    agents_used: List[str]
    status: str
    duration: Optional[float] = None

class SessionSummary(BaseModel):
    session_id: str
    summary: str
    key_topics: List[str]
    participants: List[str]
    duration: float
    event_count: int
    created_at: float

class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    session_id: str = Field(default="default")
    voice: str = Field(default="en-US-JennyNeural")
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

class OrchestrationRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    workflow_type: str = Field(default="conversation")
    parameters: Dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=1, ge=1, le=10)

class OrchestrationResponse(BaseModel):
    session_id: str
    workflow_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    processing_time: float
    errors: List[str] = Field(default_factory=list)

# Align formats with Agent4 (no csv/xml)
class ExportProxyRequest(BaseModel):
    format: str = Field(default="zip", pattern="^(json|txt|html|zip|pdf|stream)$")
    title: Optional[str] = None
    include_audio_links: bool = True
    include_timestamps: bool = True

# Body model for /sessions (supports JSON body)
class CreateSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    metadata: Optional[Dict[str, Any]] = None

# ========== SQLite Manager ==========
class SessionDatabase:
    """SQLite-backed session logger with polling APIs."""
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self):
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
              CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                last_activity REAL NOT NULL,
                status TEXT DEFAULT 'active',
                metadata TEXT
              )""")
            cur.execute("""
              CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT NOT NULL,
                agent TEXT NOT NULL,
                timestamp REAL NOT NULL,
                metadata TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
              )""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
            conn.commit()
        logger.info(f"✅ Database initialized at {self.db_path}")

    # --- Sessions ---
    def upsert_session(self, session_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        now = time.time()
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
              INSERT INTO sessions (session_id, created_at, last_activity, status, metadata)
              VALUES (?, ?, ?, 'active', ?)
              ON CONFLICT(session_id) DO UPDATE SET last_activity=excluded.last_activity
            """, (session_id, now, now, json.dumps(metadata) if metadata else None))
            conn.commit()

    def set_session_status(self, session_id: str, status: str) -> None:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE sessions SET status=?, last_activity=? WHERE session_id=?", (status, time.time(), session_id))
            conn.commit()

    def list_sessions(self, limit: int = 50) -> List[SessionInfo]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT s.session_id, s.created_at, s.last_activity, s.status,
                       (SELECT COUNT(*) FROM events e WHERE e.session_id = s.session_id) AS total_events,
                       (SELECT GROUP_CONCAT(DISTINCT e.agent) FROM events e WHERE e.session_id = s.session_id) AS agents
                FROM sessions s
                ORDER BY s.last_activity DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            out: List[SessionInfo] = []
            for sid, c_at, l_act, st, total, agents in rows:
                out.append(SessionInfo(
                    session_id=sid,
                    created_at=c_at,
                    last_activity=l_act,
                    total_events=total or 0,
                    agents_used=(agents.split(",") if agents else []),
                    status=st,
                    duration=(l_act - c_at) if (l_act and c_at) else None
                ))
            return out

    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
              SELECT created_at, last_activity, status,
                     (SELECT COUNT(*) FROM events WHERE session_id=s.session_id) as evt_count,
                     (SELECT GROUP_CONCAT(DISTINCT agent) FROM events WHERE session_id=s.session_id) as agents
              FROM sessions s WHERE session_id=?
            """, (session_id,))
            row = cur.fetchone()
            if not row:
                return None
            created_at, last_activity, status, count, agents_str = row
            agents = agents_str.split(",") if agents_str else []
            return SessionInfo(
                session_id=session_id,
                created_at=created_at,
                last_activity=last_activity,
                total_events=count or 0,
                agents_used=agents,
                status=status,
                duration=(last_activity - created_at) if (last_activity and created_at) else None
            )

    # --- Events ---
    def insert_event(self, ev: SessionEvent) -> None:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
              INSERT INTO events (session_id, event_type, content, agent, timestamp, metadata)
              VALUES (?, ?, ?, ?, ?, ?)
            """, (
                ev.session_id,
                ev.event_type,
                json.dumps(ev.content, ensure_ascii=False),
                ev.agent,
                ev.timestamp,
                json.dumps(ev.metadata, ensure_ascii=False) if ev.metadata else None
            ))
            cur.execute("UPDATE sessions SET last_activity=? WHERE session_id=?", (ev.timestamp, ev.session_id))
            conn.commit()

    def fetch_events(self, session_id: str, limit: int = 100) -> List[SessionEvent]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
              SELECT event_type, content, agent, timestamp, metadata
              FROM events WHERE session_id=? ORDER BY timestamp DESC LIMIT ?
            """, (session_id, limit))
            rows = cur.fetchall()
            events: List[SessionEvent] = []
            for event_type, content, agent, ts, meta in rows:
                events.append(SessionEvent(
                    session_id=session_id,
                    event_type=event_type,
                    content=json.loads(content or "{}"),
                    agent=agent,
                    timestamp=ts,
                    metadata=json.loads(meta) if meta else {}
                ))
            return events

    def fetch_events_since(self, session_id: str, since_ts: float, limit: int) -> List[SessionEvent]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
              SELECT event_type, content, agent, timestamp, metadata
              FROM events
              WHERE session_id=? AND timestamp > ?
              ORDER BY timestamp ASC
              LIMIT ?
            """, (session_id, since_ts, limit))
            rows = cur.fetchall()
            out: List[SessionEvent] = []
            for event_type, content, agent, ts, meta in rows:
                out.append(SessionEvent(
                    session_id=session_id,
                    event_type=event_type,
                    content=json.loads(content or "{}"),
                    agent=agent,
                    timestamp=ts,
                    metadata=json.loads(meta) if meta else {}
                ))
            return out

    def error_summary(self, session_id: str) -> Dict[str, Any]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("""
              SELECT event_type, COUNT(*)
              FROM events
              WHERE session_id=? AND (event_type LIKE '%_error' OR event_type='error' OR event_type='workflow_error')
              GROUP BY event_type
            """, (session_id,))
            rows = cur.fetchall()
            total = sum(count for _, count in rows) if rows else 0
            return {"total_errors": total, "by_type": {et: c for et, c in rows}}

    def cleanup_older_than(self, cutoff_ts: float) -> Dict[str, int]:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM events WHERE timestamp < ?", (cutoff_ts,))
            deleted_events = cur.rowcount
            cur.execute("DELETE FROM sessions WHERE last_activity < ?", (cutoff_ts,))
            deleted_sessions = cur.rowcount
            conn.commit()
            return {"events_deleted": deleted_events or 0, "sessions_deleted": deleted_sessions or 0}

    # --- Metrics helpers ---
    def count_sessions(self) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM sessions")
            (n,) = cur.fetchone()
            return int(n or 0)

    def count_events(self) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM events")
            (n,) = cur.fetchone()
            return int(n or 0)

# ========== Orchestrator ==========
class PodcastOrchestrator:
    """Workflow stubs (round-robin etc. handled by graph.py)."""
    def __init__(self, db: SessionDatabase):
        self.db = db

    async def orchestrate(self, req: OrchestrationRequest) -> OrchestrationResponse:
        start = time.time()
        wid = f"wf_{uuid.uuid4().hex[:8]}"
        try:
            await self._log(req.session_id, "workflow_started", {"workflow_id": wid, "type": req.workflow_type, "parameters": req.parameters})
            if req.workflow_type == "conversation":
                result = {"status": "coordinated", "note": "Delegated to graph orchestrator"}
            elif req.workflow_type == "analysis":
                result = {"status": "completed", "analysis_results": "OK"}
            elif req.workflow_type == "export":
                result = {"status": "completed", "export_formats": ["json", "txt", "html", "zip"]}
            else:
                raise ValueError(f"Unknown workflow type: {req.workflow_type}")

            took = time.time() - start
            await self._log(req.session_id, "workflow_completed", {"workflow_id": wid, "result": result, "processing_time": took})
            return OrchestrationResponse(session_id=req.session_id, workflow_id=wid, status="completed", result=result, processing_time=took)
        except Exception as e:
            took = time.time() - start
            await self._log(req.session_id, "workflow_error", {"workflow_id": wid, "error": str(e)})
            return OrchestrationResponse(session_id=req.session_id, workflow_id=wid, status="error", result=None, processing_time=took, errors=[str(e)])

    async def _log(self, session_id: str, event_type: str, content: Dict[str, Any]):
        await anyio.to_thread.run_sync(self.db.insert_event, SessionEvent(session_id=session_id, event_type=event_type, content=content, agent="agent5_orchestrator"))

# ========== Agent ==========
class Agent5:
    """Logger + orchestration helper with polling APIs, cleanup, Graph/Export glue, and ingest."""
    def __init__(self):
        self._start_time = time.time()
        self.db = SessionDatabase(settings.DATABASE_PATH)
        self.orchestrator = PodcastOrchestrator(self.db)

        # LLM (lazy init; used for /v1/chat helper)
        self.llm = None
        self._SystemMessage = None
        self._HumanMessage = None

        # Graph ingest state: last turn id per session (in-memory)
        self._ingest_enabled = settings.GRAPH_INGEST_ENABLED
        self._last_seen_turn_id: Dict[str, Any] = {}

        # Lightweight in-memory metrics
        self._metrics = {
            "requests_total": 0,
            "errors_total": 0,
            "latency_count": 0,
            "latency_sum": 0.0,
            "latency_max": 0.0,
        }
        self._metrics_lock = asyncio.Lock()
        logger.info("✅ Agent5 initialized")

    # ---- Logging primitives ----
    async def create_session(self, session_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if not _valid_session_id(session_id):
            raise ValueError("Invalid session_id format")
        await anyio.to_thread.run_sync(self.db.upsert_session, session_id, metadata or {})

    async def set_status(self, session_id: str, status: str) -> None:
        await anyio.to_thread.run_sync(self.db.set_session_status, session_id, status)

    async def log_event(self, event: SessionEvent) -> bool:
        try:
            if not _valid_session_id(event.session_id):
                raise ValueError("Invalid session_id format")
            await anyio.to_thread.run_sync(self.db.upsert_session, event.session_id, {})
            await anyio.to_thread.run_sync(self.db.insert_event, event)
            return True
        except Exception as e:
            logger.error(f"❌ log_event failed: {e}")
            return False

    async def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        return await anyio.to_thread.run_sync(self.db.get_session_info, session_id)

    async def list_sessions(self, limit: int = 50) -> List[SessionInfo]:
        return await anyio.to_thread.run_sync(self.db.list_sessions, limit)

    async def get_session_events(self, session_id: str, limit: int = 100) -> List[SessionEvent]:
        limit = min(limit, settings.MAX_LOG_ENTRIES_RETURNED)
        return await anyio.to_thread.run_sync(self.db.fetch_events, session_id, limit)

    async def get_events_since(self, session_id: str, since_ts: float, limit: int) -> List[SessionEvent]:
        limit = min(limit, settings.MAX_LOG_ENTRIES_RETURNED)
        return await anyio.to_thread.run_sync(self.db.fetch_events_since, session_id, since_ts, limit)

    async def get_error_summary(self, session_id: str) -> Dict[str, Any]:
        return await anyio.to_thread.run_sync(self.db.error_summary, session_id)

    async def cleanup(self) -> Dict[str, int]:
        cutoff = time.time() - settings.LOG_RETENTION_DAYS * 86400
        return await anyio.to_thread.run_sync(self.db.cleanup_older_than, cutoff)

    # ---- Orchestration ----
    async def orchestrate(self, request: OrchestrationRequest) -> OrchestrationResponse:
        return await self.orchestrator.orchestrate(request)

    # ---- LLM helper ----
    def _ensure_llm(self):
        if self.llm is not None:
            return
        if not (settings.AZURE_OPENAI_KEY and settings.AZURE_OPENAI_ENDPOINT):
            raise RuntimeError("LLM not configured (AZURE_OPENAI_KEY/ENDPOINT missing)")
        from langchain_community.chat_models import AzureChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        self._SystemMessage = SystemMessage
        self._HumanMessage = HumanMessage
        self.llm = AzureChatOpenAI(
            deployment_name=settings.AZURE_OPENAI_DEPLOYMENT,
            api_key=settings.AZURE_OPENAI_KEY,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.OPENAI_API_VERSION,
            temperature=0.7,
            timeout=settings.AGENT_TIMEOUT
        )

    # ---- Chat helper (philosopher persona) ----
    @retry(stop=stop_after_attempt(settings.MAX_RETRIES),
           wait=wait_exponential(multiplier=1, min=1, max=8))
    async def handle_chat(self, request: ChatRequest) -> ChatResponse:
        t0 = time.time()
        try:
            await self.log_event(SessionEvent(
                session_id=request.session_id, event_type="chat_request",
                content={"text": (request.text[:200] + "...") if len(request.text) > 200 else request.text},
                agent="agent5"
            ))
            self._ensure_llm()
            sys = self._SystemMessage(content=self._build_system_prompt(request))
            human = self._HumanMessage(content=request.text)
            result = await self.llm.agenerate([[sys, human]])
            text = (result.generations[0][0].text or "").strip()
            await self.log_event(SessionEvent(
                session_id=request.session_id, event_type="chat_response",
                content={"response": (text[:200] + "...") if len(text) > 200 else text},
                agent="agent5"
            ))
            return ChatResponse(
                response=text or "(no content)", audio_path=None, session_id=request.session_id, status="success",
                processing_time=round(time.time() - t0, 3),
                agent_info={"agent": "agent5", "conversation_mode": request.conversation_mode,
                            "is_interruption": request.is_interruption, "voice": request.voice},
                turn_number=request.turn_number
            )
        except Exception as e:
            await self.log_event(SessionEvent(
                session_id=request.session_id, event_type="chat_error",
                content={"error": str(e)}, agent="agent5"
            ))
            return ChatResponse(
                response=f"Error: {e}", session_id=request.session_id, status="error",
                processing_time=round(time.time() - t0, 3)
            )

    def _build_system_prompt(self, request: ChatRequest) -> str:
        base = (
            "You are Agent5, a philosopher and deep thinker for a podcast panel.\n"
            "- Offer concise (2–3 sentences) reflections.\n"
            "- Ask thought-provoking questions.\n"
            "- Maintain a contemplative, helpful tone."
        )
        if request.is_interruption:
            base += "\n(Respond to an interruption with brief clarity.)"
        if request.turn_number and request.max_turns:
            base += f"\n(Turn {request.turn_number} of {request.max_turns}.)"
        if request.conversation_context:
            base += f"\nContext:\n{request.conversation_context}"
        return base

    # ---- Graph helpers ----
    @retry(stop=stop_after_attempt(settings.MAX_RETRIES),
           wait=wait_exponential(multiplier=1, min=1, max=8))
    async def fetch_graph_state(self, session_id: str) -> Dict[str, Any]:
        async with _client() as client:
            r = await client.get(f"{settings.GRAPH_URL}/session/{session_id}/state")
            if r.status_code != 200:
                raise RuntimeError(f"Graph state fetch failed ({r.status_code})")
            return r.json()

    @retry(stop=stop_after_attempt(settings.MAX_RETRIES),
           wait=wait_exponential(multiplier=1, min=1, max=8))
    async def fetch_mute_status(self, session_id: str, agent: Optional[str] = None) -> bool:
        params = {"agent": agent} if agent else None
        async with _client() as client:
            r = await client.get(f"{settings.GRAPH_URL}/session/{session_id}/mute-status", params=params)
            if r.status_code != 200:
                return False
            return bool(r.json().get("muted", False))

    @retry(stop=stop_after_attempt(settings.MAX_RETRIES),
           wait=wait_exponential(multiplier=1, min=1, max=8))
    async def proxy_export_to_agent4(self, session_id: str, format_: str, title: Optional[str],
                                     include_audio: bool, include_ts: bool) -> Dict[str, Any]:
        # DEBUG: Building export payload for Agent4
        state = await self.fetch_graph_state(session_id)
        history = state.get("history", [])
        convo = []
        for t in history:
            convo.append({
                "turn_id": t.get("turn_id"),
                "speaker": t.get("agent"),
                "message": {
                    "content": t.get("response") or "",
                    "timestamp": t.get("timestamp") or 0,
                    "audio_path": t.get("audio_path") or ""
                }
            })
        payload = {
            "format": format_,
            "title": title or f"podcast_session_{session_id}",
            "session_id": session_id,
            "include_audio_links": include_audio,
            "include_timestamps": include_ts,
            "content": {
                "topic": state.get("topic"),
                "conversation_history": convo,
                "agent_order": state.get("agent_order"),
                "rounds_completed": state.get("current_round")
            },
            "metadata": {
                "voices": state.get("voices", {}),
                "mute": state.get("mute", {}),
                "ended": state.get("ended", False)
            }
        }
        async with _client() as client:
            r = await client.post(f"{settings.AGENT4_URL}/export", json=payload)
            if r.status_code != 200:
                raise RuntimeError(f"Agent4 export failed ({r.status_code}): {r.text[:200]}")
            return r.json()

    # ---- Graph ingest (mirror history into DB) ----
    async def ingest_graph_once(self, session_id: str) -> int:
        """
        Pulls graph state and logs any new turns into DB as 'graph_turn_ingested'.
        Returns number of new turns ingested.
        """
        try:
            state = await self.fetch_graph_state(session_id)
        except Exception as e:
            logger.debug(f"ingest: state fetch failed for {session_id}: {e}")
            return 0

        history: List[Dict[str, Any]] = state.get("history", []) or []
        if not history:
            return 0

        def _to_int(x: Any) -> Optional[int]:
            try:
                return int(x)
            except Exception:
                return None

        last_seen = self._last_seen_turn_id.get(session_id)
        last_seen_int = _to_int(last_seen)
        new_count = 0

        for turn in history:
            tid = turn.get("turn_id")
            tid_int = _to_int(tid)

            # DEBUG: defensively skip duplicates; prefer numeric compare if possible
            if last_seen is not None:
                if tid_int is not None and last_seen_int is not None:
                    if tid_int <= last_seen_int:
                        continue
                else:
                    if tid == last_seen:
                        continue

            ev = SessionEvent(
                session_id=session_id,
                event_type="graph_turn_ingested",
                content={
                    "turn_id": tid,
                    "agent": turn.get("agent"),
                    "response": turn.get("response"),
                    "audio_path": turn.get("audio_path"),
                    "timestamp_graph": turn.get("timestamp"),
                },
                agent="agent5_ingest",
                timestamp=time.time()
            )
            await self.log_event(ev)
            new_count += 1

        # Update marker (prefer numeric if available)
        last_tid = history[-1].get("turn_id")
        self._last_seen_turn_id[session_id] = _to_int(last_tid) if _to_int(last_tid) is not None else last_tid
        return new_count

    async def _ingest_loop(self):
        """Periodically scans active sessions and ingests graph history."""
        logger.info(f"🧲 Graph ingest loop started (enabled={self._ingest_enabled}, interval={settings.GRAPH_POLL_INTERVAL_SEC}s)")
        while True:
            try:
                if self._ingest_enabled:
                    sessions = await self.list_sessions(limit=200)
                    active = [s.session_id for s in sessions if s.status == "active"]
                    for sid in active:
                        try:
                            added = await self.ingest_graph_once(sid)
                            if added:
                                logger.debug(f"ingest: +{added} turn(s) for {sid}")
                        except Exception as e:
                            logger.debug(f"ingest: error for {sid}: {e}")
                await asyncio.sleep(settings.GRAPH_POLL_INTERVAL_SEC)
            except Exception as e:
                logger.error(f"❌ ingest loop error: {e}")
                await asyncio.sleep(max(2.0, settings.GRAPH_POLL_INTERVAL_SEC))

# ========== FastAPI App ==========
app = FastAPI(
    title="📊 Agent5 - Logger & Orchestrator",
    description="Session logging, polling APIs, summaries, Graph/Export glue, and Graph ingest",
    version="2.4.1"
)

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

# Initialize agent
agent = Agent5()

_last_health_log: float = 0.0

# ---- Startup background tasks ----
@app.on_event("startup")
async def _startup():
    # periodic DB cleanup
    async def _periodic_cleanup():
        while True:
            try:
                stats = await agent.cleanup()
                if (stats.get("events_deleted") or 0) or (stats.get("sessions_deleted") or 0):
                    logger.info(f"🗑️ Cleanup: {stats}")
            except Exception as e:
                logger.error(f"❌ Cleanup task failed: {e}")
            await asyncio.sleep(settings.CLEANUP_INTERVAL)

    asyncio.create_task(_periodic_cleanup())
    if settings.GRAPH_INGEST_ENABLED:
        asyncio.create_task(agent._ingest_loop())
    asyncio.create_task(_retention_worker())

# ========== API Endpoints ==========

# --- Sessions ---
@app.post("/sessions")
async def create_session(
    session_id: Optional[str] = Query(None, description="Optional if sending JSON body"),
    payload: Optional[CreateSessionRequest] = Body(None)
):
    """Create session; accepts either ?session_id=... or JSON body {"session_id": "...", "metadata": {...}}."""
    sid = payload.session_id if payload and payload.session_id else session_id
    if not _valid_session_id(sid):
        raise HTTPException(status_code=400, detail="Invalid or missing session_id")
    meta = payload.metadata if payload and payload.metadata is not None else {}
    logger.info(f"[Agent5] create_session sid={sid}")
    await agent.create_session(sid, meta)
    return {"status": "success", "session_id": sid}

@app.get("/sessions")
async def list_sessions(limit: int = Query(50, ge=1, le=500)):
    items = await agent.list_sessions(limit)
    return {"sessions": [i.dict() for i in items]}

@app.post("/sessions/{session_id}/status")
async def set_session_status(session_id: str, status: str = Query(..., pattern="^(active|paused|ended)$")):
    await agent.set_status(session_id, status)
    return {"status": "success", "session_id": session_id, "new_status": status}

@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    info = await agent.get_session_info(session_id)
    if not info:
        raise HTTPException(status_code=404, detail="Session not found")
    return info

# --- Simple marker/note event ---
@app.post("/sessions/{session_id}/mark")
async def add_marker(session_id: str, note: str = Query(..., min_length=1, max_length=500)):
    ok = await agent.log_event(SessionEvent(session_id=session_id, event_type="note", content={"note": note}, agent="agent5"))
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to log note")
    return {"status": "success", "session_id": session_id}

# --- Events (append & read) ---
@app.post("/sessions/{session_id}/events")
async def log_event(session_id: str, event: SessionEvent):
    if event.session_id != session_id:
        raise HTTPException(status_code=400, detail="session_id mismatch between path and body")
    ok = await agent.log_event(event)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to log event")
    return {"status": "success", "session_id": session_id}

@app.get("/sessions/{session_id}/events")
async def get_events(session_id: str, limit: int = Query(100, ge=1, le=2000)):
    events = await agent.get_session_events(session_id, limit)
    payload = []
    for e in events:
        d = e.dict()
        d["timestamp_iso"] = datetime.fromtimestamp(e.timestamp, tz=timezone.utc).isoformat()
        payload.append(d)
    return {"session_id": session_id, "events": payload}

@app.get("/sessions/{session_id}/events/poll")
async def poll_events_since(
    session_id: str,
    since: float = Query(0.0, description="Unix timestamp (seconds)"),
    limit: int = Query(200, ge=1, le=2000)
):
    events = await agent.get_events_since(session_id, since, limit)
    latest_ts = events[-1].timestamp if events else since
    payload = []
    for e in events:
        d = e.dict()
        d["timestamp_iso"] = datetime.fromtimestamp(e.timestamp, tz=timezone.utc).isoformat()
        payload.append(d)
    return {
        "session_id": session_id,
        "events": payload,
        "latest_ts": latest_ts,
        "server_time": time.time(),
        "server_time_iso": datetime.now(timezone.utc).isoformat()
    }

@app.get("/sessions/{session_id}/errors")
async def error_summary(session_id: str):
    return await agent.get_error_summary(session_id)

@app.get("/sessions/{session_id}/stats")
async def session_stats(session_id: str):
    info = await agent.get_session_info(session_id)
    if not info:
        raise HTTPException(status_code=404, detail="Session not found")
    errs = await agent.get_error_summary(session_id)
    return {"info": info.dict(), "errors": errs}

# --- Orchestration helper ---
@app.post("/orchestrate", response_model=OrchestrationResponse)
async def orchestrate(request: OrchestrationRequest):
    return await agent.orchestrate(request)

# --- Chat helper (philosopher persona) ---
@app.post("/v1/chat", response_model=ChatResponse)
async def chat_handler(request: ChatRequest):
    return await agent.handle_chat(request)

# --- Graph glue ---
@app.get("/graph/{session_id}/state")
async def graph_state(session_id: str):
    try:
        data = await agent.fetch_graph_state(session_id)
        return {"status": "ok", "session_id": session_id, "state": data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch graph state: {e}")

@app.get("/graph/{session_id}/mute")
async def graph_mute(session_id: str, agent_name: Optional[str] = Query(None, description="e.g. agent3")):
    try:
        muted = await agent.fetch_mute_status(session_id, agent=agent_name)
        return {"session_id": session_id, "agent": agent_name, "muted": muted}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch mute status: {e}")

# --- Export proxy to Agent4 ---
@app.post("/sessions/{session_id}/export")
async def export_session(session_id: str, body: ExportProxyRequest):
    try:
        logger.info(f"[Agent5] export proxy → Agent4 session={session_id} fmt={body.format}")
        result = await agent.proxy_export_to_agent4(
            session_id=session_id,
            format_=body.format,
            title=body.title,
            include_audio=body.include_audio_links,
            include_ts=body.include_timestamps
        )
        await agent.log_event(SessionEvent(
            session_id=session_id,
            event_type="export_proxy",
            content={"format": body.format, "agent4_result": {"status": result.get("status"), "title": result.get("title")}},
            agent="agent5"
        ))
        return result
    except Exception as e:
        await agent.log_event(SessionEvent(
            session_id=session_id,
            event_type="export_proxy_error",
            content={"error": str(e), "format": body.format},
            agent="agent5"
        ))
        raise HTTPException(status_code=502, detail=f"Export failed: {e}")

# --- Ingest controls ---
@app.post("/ingest/{session_id}/once")
async def ingest_once(session_id: str):
    added = await agent.ingest_graph_once(session_id)
    return {"session_id": session_id, "ingested": added}

@app.post("/ingest/toggle")
async def ingest_toggle(enabled: bool = Query(...)):
    agent._ingest_enabled = bool(enabled)
    return {"ingest_enabled": agent._ingest_enabled, "interval_sec": settings.GRAPH_POLL_INTERVAL_SEC}

# --- Resume helper ---
@app.get("/sessions/{session_id}/resume-info")
async def resume_info(session_id: str):
    info = await agent.get_session_info(session_id)
    if not info:
        raise HTTPException(status_code=404, detail="Session not found")
    recent = await agent.get_session_events(session_id, limit=20)
    recent_payload = []
    for e in reversed(recent):
        recent_payload.append({
            "event_type": e.event_type,
            "timestamp": e.timestamp,
            "timestamp_iso": datetime.fromtimestamp(e.timestamp, tz=timezone.utc).isoformat(),
            "agent": e.agent,
            "content": e.content
        })
    return {"session": info.dict(), "recent_tail": recent_payload}

# --- Metrics endpoint ---
@app.get("/metrics")
async def metrics():
    try:
        # sessions count best-effort via DB (fallback to 0)
        import sqlite3
        count = 0
        try:
            conn = sqlite3.connect(settings.DATABASE_PATH)
            cur = conn.cursor()
            try:
                cur.execute("SELECT COUNT(1) FROM sessions WHERE status != 'ended'")
                row = cur.fetchone()
                if row and row[0] is not None:
                    count = int(row[0])
            except Exception:
                try:
                    cur.execute("SELECT COUNT(1) FROM sessions")
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        count = int(row[0])
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return {
            "sessions": count,
            "db_path": settings.DATABASE_PATH,
            "poll_interval": settings.GRAPH_POLL_INTERVAL_SEC,
            "retention_days": settings.LOG_RETENTION_DAYS,
            "time": int(time.time()),
        }
    except Exception as e:
        logger.warning(f"/metrics failed: {e}")
        return {
            "sessions": 0,
            "db_path": settings.DATABASE_PATH,
            "poll_interval": settings.GRAPH_POLL_INTERVAL_SEC,
            "retention_days": settings.LOG_RETENTION_DAYS,
            "time": int(time.time()),
        }

# --- Health endpoint ---
_last_health_log: float = 0.0
@app.get("/health")
async def health():
    global _last_health_log
    try:
        now = time.time()
        if now - _last_health_log < settings.HEALTH_LOG_DEBOUNCE_SECONDS:
            return {"status": "ok"}
        sid = f"health_{int(time.time())}"
        await agent.create_session(sid, {"health": True})
        ok = await agent.log_event(SessionEvent(session_id=sid, event_type="health_ping", content={"ok": True}))
        _last_health_log = now
        return {
            "status": "healthy" if ok else "degraded",
            "service": "Agent5 - Logger & Orchestrator",
            "version": "2.4.1",
            "uptime_sec": round(time.time() - agent._start_time, 2),
            "db_path": settings.DATABASE_PATH,
            "graph_url": settings.GRAPH_URL,
            "agent4_url": settings.AGENT4_URL,
            "ingest_enabled": agent._ingest_enabled,
            "poll_interval_sec": settings.GRAPH_POLL_INTERVAL_SEC
        }
    except Exception as e:
        logger.error(f"❌ Health failed: {e}")
        return {"status": "unhealthy", "error": str(e)}

@app.get("/")
async def root():
    return {
        "service": "Agent5 - Logger & Orchestrator",
        "version": "2.4.1",
        "endpoints": {
            "create_session": "POST /sessions (query or JSON body)",
            "list_sessions": "GET /sessions",
            "set_status": "POST /sessions/{session_id}/status",
            "get_session": "GET /sessions/{session_id}",
            "mark": "POST /sessions/{session_id}/mark?note=..",
            "log_event": "POST /sessions/{session_id}/events",
            "get_events": "GET /sessions/{session_id}/events?limit=..",
            "poll_events_since": "GET /sessions/{session_id}/events/poll?since=..&limit=..",
            "error_summary": "GET /sessions/{session_id}/errors",
            "session_stats": "GET /sessions/{session_id}/stats",
            "orchestrate": "POST /orchestrate",
            "chat": "POST /v1/chat",
            "graph_state": "GET /graph/{session_id}/state",
            "graph_mute": "GET /graph/{session_id}/mute?agent_name=..",
            "export_session": "POST /sessions/{session_id}/export",
            "ingest_once": "POST /ingest/{session_id}/once",
            "ingest_toggle": "POST /ingest/toggle?enabled=true|false",
            "resume_info": "GET /sessions/{session_id}/resume-info",
            "health": "GET /health",
            "metrics": "GET /metrics"
        },
        "features": [
            "Session creation & status (active/paused/ended)",
            "Append events and fetch with polling (since timestamp)",
            "Error summary & session stats",
            "Periodic cleanup by retention window",
            "Philosopher chat helper (System+Human prompt, retries)",
            "Decorator: @log_execution(agent5) for auto-logging",
            "WAL-enabled SQLite + non-blocking DB access",
            "Graph state/mute integration",
            "Agent4 export proxy (JSON/TXT/HTML/ZIP/PDF)",
            "Graph ingest: mirrors conversation history into DB",
            "Session list + resume helpers + manual notes"
        ]
    }

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
    "agent5",
    required_vars=["GRAPH_URL","AGENT4_URL","DATABASE_PATH"],
    url_vars=["GRAPH_URL","AGENT4_URL"]
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007, log_level="info")
