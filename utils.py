# utils.py – 🔧 Production-Grade Utility Functions (v2.2.2)

import os
import re
import logging
import asyncio
import time
from typing import Optional, Dict, Any

import httpx
import azure.cognitiveservices.speech as speechsdk
from azure.identity import ClientSecretCredential
from pydantic_settings import BaseSettings
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential

__all__ = [
    "settings",
    "logger",
    "get_speech_config",
    "transcribe_audio",
    "parse_voice_command",
    "validate_audio_file",
    "clean_text_for_processing",
    "validate_session_id",
    "sanitize_filename",
    "format_timestamp",
    "get_agent_voice",
    "get_agent_personality",
    "create_error_response",
    "handle_service_error",
    "check_service_health",
    "check_all_services",
    "validate_configuration",
    "is_session_muted",
]

# ========== Settings ==========
class Settings(BaseSettings):
    CLIENT_ID: str
    CLIENT_SECRET: str
    TENANT_ID: str
    RESOURCE_ID: str
    SPEECH_REGION: str = "eastus"

    AZURE_OPENAI_KEY: str
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4o"
    OPENAI_API_VERSION: str = "2024-05-01-preview"

    AGENT1_URL: str = "http://localhost:8001"
    AGENT2_URL: str = "http://localhost:8002"
    AGENT3_URL: str = "http://localhost:8004"
    AGENT4_URL: str = "http://localhost:8006"
    AGENT5_URL: str = "http://localhost:8007"
    GRAPH_URL: str  = "http://localhost:8008"

    MAX_AUDIO_SIZE: int = 5 * 1024 * 1024
    AUDIO_CACHE_TTL: int = 3600
    AGENT_TIMEOUT: float = 15.0
    MAX_TEXT_LENGTH: int = 10000
    MAX_RETRIES: int = 3

    AZURE_SPEECH_KEY: Optional[str] = None

    class Config:
        env_file = ".env"
        extra = "ignore"

try:
    settings = Settings()
except Exception as e:
    print(f"❌ Configuration Error: {e}")
    print("Please ensure your .env includes required variables.")
    raise

# ========== Logger ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("Utils")

# ========== HTTP helpers ==========
def _async_client(timeout: Optional[float] = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout or settings.AGENT_TIMEOUT)

# ========== Speech Configuration ==========
def get_speech_config() -> speechsdk.SpeechConfig:
    try:
        if all([settings.CLIENT_ID, settings.CLIENT_SECRET, settings.TENANT_ID, settings.RESOURCE_ID]):
            try:
                credential = ClientSecretCredential(
                    tenant_id=settings.TENANT_ID,
                    client_id=settings.CLIENT_ID,
                    client_secret=settings.CLIENT_SECRET,
                )
                token = credential.get_token("https://cognitiveservices.azure.com/.default")
                auth_token = f"aad#{settings.RESOURCE_ID}#{token.token}"
                cfg = speechsdk.SpeechConfig(auth_token=auth_token, region=settings.SPEECH_REGION)
                logger.info("✅ Using Azure AD authentication for Speech Service")
                return cfg
            except Exception as e:
                logger.warning(f"⚠️ Azure AD auth failed; will try key fallback. Details: {e}")

        if settings.AZURE_SPEECH_KEY:
            cfg = speechsdk.SpeechConfig(
                subscription=settings.AZURE_SPEECH_KEY,
                region=settings.SPEECH_REGION
            )
            logger.info("✅ Using subscription key for Speech Service")
            return cfg

        raise ValueError("No valid Speech auth method available (AAD and key both unavailable).")

    except Exception as e:
        logger.error(f"❌ Failed to create SpeechConfig: {e}")
        raise

# ========== Enhanced Transcription ==========
async def transcribe_audio(file_path: str) -> str:
    if not file_path or not os.path.exists(file_path):
        logger.error(f"❌ Audio file not found: {file_path}")
        return ""

    try:
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            logger.error("❌ Audio file is empty")
            return ""
        if file_size > settings.MAX_AUDIO_SIZE:
            logger.error(f"❌ Audio file too large: {file_size} > {settings.MAX_AUDIO_SIZE}")
            return ""
    except Exception as e:
        logger.error(f"❌ Cannot stat audio file: {e}")
        return ""

    def _transcribe_sync() -> str:
        try:
            cfg = get_speech_config()
            audio_cfg = speechsdk.audio.AudioConfig(filename=file_path)
            recognizer = speechsdk.SpeechRecognizer(speech_config=cfg, audio_config=audio_cfg)
            result = recognizer.recognize_once()
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                logger.info(f"✅ Transcribed audio ({len(result.text)} chars)")
                return result.text or ""
            if result.reason == speechsdk.ResultReason.NoMatch:
                logger.warning("⚠️ No speech recognized")
                return ""
            if result.reason == speechsdk.ResultReason.Canceled:
                c = result.cancellation_details
                logger.error(f"❌ Recognition canceled: {c.reason} ({c.error_details or 'no details'})")
                return ""
            logger.error(f"❌ Recognition failed: {result.reason}")
            return ""
        except Exception as e:
            logger.error(f"❌ Transcription failed: {e}")
            return ""

    logger.info(f"🎤 Transcribing: {file_path}")
    try:
        text = await asyncio.to_thread(_transcribe_sync)
        return text.strip()
    except Exception as e:
        logger.error(f"❌ Async transcription error: {e}")
        return ""

# ========== Voice Command Parser ==========
def parse_voice_command(text: str) -> str:
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    text = text.strip()
    if not text:
        return ""

    command_mappings = {
        "summarize": "/summarize",
        "summary": "/summarize",
        "sum": "/summarize",
        "polish": "/polish",
        "improve": "/polish",
        "enhance": "/polish",
        "topics": "/topics",
        "analyze": "/topics",
        "segments": "/topics",
        "export": "/export",
        "save": "/export",
        "download": "/export",
        "interrupt": "/interrupt",
        "stop": "/interrupt",
        "pause": "/interrupt",
    }
    lower = text.lower()

    for trigger, cmd in command_mappings.items():
        if lower == trigger or lower.startswith(trigger + " "):
            rest = text[len(trigger):].strip()
            return f"{cmd} {rest}" if rest else cmd

    for pattern in (r"([A-E]):\s*(.+)", r"agent\s+([A-E]):\s*(.+)", r"([A-E])\s+(.+)"):
        m = re.match(pattern, text, flags=re.IGNORECASE)
        if m:
            agent_letter = m.group(1).upper()
            message = m.group(2).strip()
            return f"{agent_letter}: {message}"

    for pattern in (r"export\s+(.+)", r"save\s+as\s+(.+)", r"download\s+(.+)"):
        m = re.match(pattern, lower)
        if m:
            return f"/export {m.group(1)}"

    for pattern in (r"remember\s+(.+)", r"store\s+(.+)", r"save\s+memory\s+(.+)"):
        m = re.match(pattern, lower)
        if m:
            return f"/memory set context {m.group(1)}"

    return text

# ========== Validators & Sanitizers ==========
def validate_audio_file(file_path: str) -> Dict[str, Any]:
    out = {"valid": False, "error": None, "file_size": 0, "file_exists": False}
    try:
        if not file_path:
            out["error"] = "No file path provided"
            return out
        if not os.path.exists(file_path):
            out["error"] = f"File not found: {file_path}"
            return out
        out["file_exists"] = True
        size = os.path.getsize(file_path)
        out["file_size"] = size
        if size == 0:
            out["error"] = "File is empty"
            return out
        if size > settings.MAX_AUDIO_SIZE:
            out["error"] = f"File too large: {size} bytes (max: {settings.MAX_AUDIO_SIZE})"
            return out
        out["valid"] = True
        return out
    except Exception as e:
        out["error"] = f"Validation failed: {e}"
        return out

def clean_text_for_processing(text: str, max_length: int = 5000) -> str:
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) > max_length:
        text = text[:max_length] + "..."
        logger.warning(f"⚠️ Text truncated to {max_length} characters")
    return text

def validate_session_id(session_id: str) -> bool:
    return bool(session_id and re.match(r"^[a-zA-Z0-9_-]+$", session_id))

def sanitize_filename(filename: str) -> str:
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename or "")
    return filename[:100] if len(filename) > 100 else filename

def format_timestamp(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except Exception:
        return "00:00:00"

def get_agent_voice(agent_letter: str) -> str:
    voices = {
        "A": "en-US-AriaNeural",
        "B": "en-GB-RyanNeural",
        "C": "en-IN-PrabhatNeural",
        "D": "en-US-GuyNeural",
        "E": "en-US-JennyNeural",
    }
    return voices.get((agent_letter or "").upper(), "en-US-AriaNeural")

def get_agent_personality(agent_letter: str) -> str:
    personas = {
        "A": "enthusiastic host and moderator",
        "B": "analytical expert and researcher",
        "C": "creative storyteller and entertainer",
        "D": "technical specialist and fact-checker",
        "E": "philosopher and deep thinker",
    }
    return personas.get((agent_letter or "").upper(), "helpful assistant")

# ========== Errors ==========
def create_error_response(error_message: str, context: str = "") -> Dict[str, Any]:
    return {
        "error": True,
        "message": error_message,
        "context": context,
        "timestamp": time.time(),
        "suggestions": [
            "Check your Azure credentials",
            "Verify service connectivity",
            "Check input format and size",
            "Review logs for detailed errors",
        ],
    }

def handle_service_error(error: Exception, service_name: str) -> Dict[str, Any]:
    msg = str(error)
    lower = msg.lower()
    if "timeout" in lower:
        return create_error_response(f"{service_name} request timed out", "Try again or check service status")
    if "connection" in lower:
        return create_error_response(f"Cannot connect to {service_name}", "Ensure the service is running and reachable")
    if "authentication" in lower or "unauthorized" in lower or "forbidden" in lower:
        return create_error_response(f"Authentication failed for {service_name}", "Verify your credentials and permissions")
    return create_error_response(f"{service_name} error: {msg}", "Check logs for more details")

# ========== Health Checks ==========
@retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _get_json_with_retries(url: str, *, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        # Retry on 5xx
        if r.status_code >= 500:
            raise RuntimeError(f"transient {r.status_code}")
        try:
            body = r.json()
        except Exception:
            body = {}
        return {"status_code": r.status_code, "body": body}

async def check_service_health(service_url: str, timeout: float = 5.0) -> Dict[str, Any]:
    t0 = time.time()
    try:
        resp = await _get_json_with_retries(f"{service_url}/health", timeout=timeout)
        latency = time.time() - t0
        return {
            "healthy": (resp["status_code"] == 200),
            "status_code": resp["status_code"],
            "response_time": round(latency, 3),
            "body": resp["body"],
        }
    except Exception as e:
        return {
            "healthy": False,
            "error": str(e),
            "response_time": round(time.time() - t0, 3),
        }

async def check_all_services() -> Dict[str, Any]:
    services = {
        "agent1": settings.AGENT1_URL,
        "agent2": settings.AGENT2_URL,
        "agent3": settings.AGENT3_URL,
        "agent4": settings.AGENT4_URL,
        "agent5": settings.AGENT5_URL,
        "graph":  settings.GRAPH_URL,
    }
    tasks = {name: asyncio.create_task(check_service_health(url)) for name, url in services.items()}
    results = {}
    for name, task in tasks.items():
        results[name] = await task
    return results

# ========== Graph integration ==========
async def is_session_muted(session_id: str, agent: Optional[str] = None) -> bool:
    """
    Ask Graph's /session/{session_id}/mute-status if this session (or agent) is muted.
    Returns True when either the session is paused/ended or the agent is muted.
    """
    if not session_id:
        return False
    try:
        params = {"agent": agent} if agent else None
        url = f"{settings.GRAPH_URL}/session/{session_id}/mute-status"
        resp = await _get_json_with_retries(url, params=params, timeout=settings.AGENT_TIMEOUT)
        if resp["status_code"] == 200:
            return bool(resp["body"].get("muted", False))
        # No retry on 4xx; treat as unmuted/unknown
        logger.warning(f"mute-status non-200 ({resp['status_code']})")
    except Exception as e:
        logger.warning(f"Mute check failed: {e}")
    return False

# ========== Configuration Validation ==========
def validate_configuration() -> Dict[str, Any]:
    out = {"valid": True, "errors": [], "warnings": []}

    required = [
        "AZURE_OPENAI_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "CLIENT_ID",
        "CLIENT_SECRET",
        "TENANT_ID",
        "RESOURCE_ID",
    ]
    for key in required:
        if not getattr(settings, key, None):
            out["errors"].append(f"Missing required setting: {key}")
            out["valid"] = False

    if not settings.AZURE_SPEECH_KEY:
        out["warnings"].append("AZURE_SPEECH_KEY not set – will use Azure AD auth only")

    for url_key in ["AGENT1_URL", "AGENT2_URL", "AGENT3_URL", "AGENT4_URL", "AGENT5_URL", "GRAPH_URL"]:
        url = getattr(settings, url_key, "")
        if not (isinstance(url, str) and url.startswith("http")):
            out["warnings"].append(f"Invalid service URL: {url_key}={url!r}")

    return out

# ========== Main ==========
if __name__ == "__main__":
    cfg = validate_configuration()
    if not cfg["valid"]:
        print("❌ Configuration validation failed:")
        for e in cfg["errors"]:
            print(f"  - {e}")
        exit(1)

    if cfg["warnings"]:
        print("⚠️ Configuration warnings:")
        for w in cfg["warnings"]:
            print(f"  - {w}")

    print("✅ Configuration validation passed")


def get_agent_voice(agent: str) -> str:
    """Curated voice selections for professional podcasting"""
    voice_map = {
        "agent1": {  # Host
            "voice": "en-US-AriaNeural",
            "style": "friendly",
            "styledegree": 1.5
        },
        "agent2": {  # Analyst
            "voice": "en-GB-RyanNeural",
            "style": "cheerful",
            "styledegree": 1.0
        },
        "agent3": {  # Storyteller
            "voice": "en-IN-PrabhatNeural",
            "style": "warm",
            "styledegree": 1.3
        },
        "agent5": {  # Philosopher
            "voice": "en-US-JennyNeural",
            "style": "calm",
            "styledegree": 0.8
        }
    }
    return voice_map.get(agent, {}).get("voice", "en-US-AriaNeural")

def get_voice_style(agent: str) -> dict:
    """Return SSML style parameters for each agent"""
    return {
        "agent1": '<mstts:express-as style="friendly" styledegree="1.5">',
        "agent2": '<mstts:express-as style="cheerful" styledegree="1.0">',
        "agent3": '<mstts:express-as style="warm" styledegree="1.3">',
        "agent5": '<mstts:express-as style="calm" styledegree="0.8">'
    }.get(agent, "")
