# agent4.py – 📦 Production-Grade Exporter (Scripts, Transcripts, HTML, ZIP) – v2.2.1
# - Accepts Graph payload and emits structured artifacts
# - Creates /exports/<safe_title_or_session>_<timestamp>/ with:
#     * summary.json (the full payload with a small header)
#     * transcript.txt (speaker-labeled, with optional timestamps & audio links)
#     * final_script.txt (cleaned “podcast script”)
#     * index.html (simple, readable HTML export)
# - Optionally returns a ZIP with all assets
# - No heavy deps; designed to “just work” out of the box

import os
import io
import json
import time
import zipfile
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# ====== Optional utils (preferred if present) ======
try:
    from utils import sanitize_filename, clean_text_for_processing
except Exception:
    import re
    def sanitize_filename(filename: str) -> str:
        filename = filename or ""
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        return filename[:100] if len(filename) > 100 else filename
    def clean_text_for_processing(text: str, max_length: int = 50000) -> str:
        if not isinstance(text, str):
            text = str(text) if text is not None else ""
        text = " ".join(text.split())
        return text[:max_length] + ("..." if len(text) > max_length else "")

# ====== Optional PDF support (ReportLab) ======
try:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

def _write_pdf_from_text(path: Path, title: str, text: str):
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("ReportLab not available for PDF export")
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=LETTER)
    width, height = LETTER
    margin = 50
    y = height - margin
    c.setTitle(title or "Export")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, y, title or "Export")
    y -= 24
    c.setFont("Helvetica", 10)
    for line in text.splitlines():
        if y < margin:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica", 10)
        c.drawString(margin, y, line[:110])
        y -= 14
    c.showPage()
    c.save()

# ========= Settings =========
class Settings(BaseSettings):
    EXPORT_DIR: str = "exports"
    DEFAULT_FORMAT: str = "zip"   # json|txt|html|zip
    MAX_TEXT_LENGTH: int = 100000
    ALLOW_LISTING: bool = True    # enable the lightweight /list endpoint
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# ========= Logging =========
logging.basicConfig(level=logging.INFO, format="%(asctime)s - agent4 - %(levelname)s - %(message)s")
logger = logging.getLogger("Agent4")

# ========= Allowed formats =========
ALLOWED_FORMATS = {"json", "txt", "html", "zip", "pdf"}

# ========= Models =========
class ExportContent(BaseModel):
    topic: Optional[str] = None
    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)
    agent_order: List[str] = Field(default_factory=list)
    rounds_completed: Optional[int] = None

class ExportRequest(BaseModel):
    format: str = Field(default=settings.DEFAULT_FORMAT, description="json|txt|html|zip|pdf|stream (stream not implemented)")
    title: Optional[str] = None
    session_id: str
    include_audio_links: bool = True
    include_timestamps: bool = True
    content: ExportContent
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ExportResponse(BaseModel):
    status: str
    session_id: str
    title: str
    dir: str
    files: Dict[str, str]  # name -> path
    zip_path: Optional[str] = None
    processing_time: float

# ========= Helpers =========
def _safe_title(session_id: str, title: Optional[str]) -> str:
    base = sanitize_filename(title or session_id or "session")
    base = base or "session"
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}"

def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)

def _mk_transcript_lines(
    convo: List[Dict[str, Any]],
    include_ts: bool,
    include_audio: bool
) -> List[str]:
    lines: List[str] = []
    for turn in convo:
        tid = turn.get("turn_id")
        speaker = str(turn.get("speaker", "agent")).strip()
        msg = turn.get("message", {}) or {}
        text = clean_text_for_processing(msg.get("content", ""), settings.MAX_TEXT_LENGTH)
        ts = msg.get("timestamp")
        audio = msg.get("audio_path") or ""
        prefix = f"[{tid}] {speaker}:"
        if include_ts and ts:
            prefix = f"{prefix} ({_fmt_ts(ts)})"
        lines.append(f"{prefix} {text}")
        if include_audio and audio:
            lines.append(f"  [audio] {audio}")
    return lines

def _fmt_ts(ts_val: Any) -> str:
    # supports float epoch or already-formatted string
    try:
        if isinstance(ts_val, (int, float)):
            return time.strftime("%H:%M:%S", time.localtime(float(ts_val)))
        return str(ts_val)
    except Exception:
        return str(ts_val)

def _mk_script(convo: List[Dict[str, Any]]) -> str:
    # A slightly cleaner, podcast-ish script: speaker label + content only
    parts: List[str] = []
    for turn in convo:
        sp = str(turn.get("speaker", "agent")).strip()
        text = clean_text_for_processing((turn.get("message") or {}).get("content", ""), 2000)
        if text:
            parts.append(f"{sp}: {text}")
    return "\n\n".join(parts)

def _mk_html(topic: Optional[str], convo: List[Dict[str, Any]], include_ts: bool, include_audio: bool) -> str:
    rows: List[str] = []
    for turn in convo:
        sp = str(turn.get("speaker", "agent")).strip()
        msg = turn.get("message", {}) or {}
        text = (msg.get("content") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ts = _fmt_ts(msg.get("timestamp"))
        audio = msg.get("audio_path") or ""
        meta = []
        if include_ts and ts:
            meta.append(ts)
        if include_audio and audio:
            meta.append(f'<a href="{audio}">audio</a>')
        meta_str = (" • ".join(meta)) if meta else ""
        rows.append(f"""
        <div class="turn">
          <div class="speaker">{sp}</div>
          <div class="text">{text}</div>
          {'<div class="meta">'+meta_str+'</div>' if meta_str else ''}
        </div>""")
    topic_html = (topic or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Podcast Export – {topic_html}</title>
  <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif; margin:40px; color:#111}}
    h1{{margin:0 0 8px 0; font-size:28px}}
    .sub{{color:#666; margin-bottom:24px}}
    .turn{{padding:14px 16px; border:1px solid #eee; border-radius:14px; margin-bottom:12px; background:#fafafa}}
    .speaker{{font-weight:600; margin-bottom:6px}}
    .text{{line-height:1.5}}
    .meta{{margin-top:8px; font-size:12px; color:#666}}
  </style>
</head>
<body>
  <h1>Podcast Export</h1>
  <div class="sub">Topic: <strong>{topic_html or '—'}</strong></div>
  {''.join(rows)}
</body>
</html>"""
    return html

def _write_text(path: Path, content: str):
    path.write_text(content, encoding="utf-8")

def _write_json(path: Path, obj: Dict[str, Any]):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def _zip_dir(src_dir: Path, out_zip: Path):
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in src_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src_dir))

# ========= FastAPI app =========
app = FastAPI(
    title="📦 Agent4 - Exporter",
    description="Generates transcript/script/HTML/ZIP from Graph conversation payloads",
    version="2.2.1"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

EXPORT_ROOT = Path(settings.EXPORT_DIR).resolve()
_ensure_dir(EXPORT_ROOT)

# ========= Core export =========
@app.post("/export", response_model=ExportResponse)
async def export_now(req: ExportRequest):
    t0 = time.time()
    try:
        # Basic validation
        convo = req.content.conversation_history or []
        if not isinstance(convo, list):
            raise HTTPException(status_code=400, detail="conversation_history must be a list")

        # Validate requested format
        fmt = (req.format or settings.DEFAULT_FORMAT).lower().strip()
        if fmt not in ALLOWED_FORMATS and fmt != "stream":
            raise HTTPException(status_code=400, detail=f"Unsupported format: {req.format}")

        safe_title = _safe_title(req.session_id, req.title)
        out_dir = EXPORT_ROOT / safe_title
        _ensure_dir(out_dir)

        # 1) summary.json (payload + small header)
        # Gather conversation stats for richer header
        speakers: List[str] = []
        voices: List[str] = []
        speeds: List[float] = []
        pitches: List[float] = []
        audio_count = 0
        total_audio_bytes = 0
        for turn in convo:
            sp = str(turn.get("speaker", "")).strip()
            if sp:
                speakers.append(sp)
            msg = turn.get("message", {}) or {}
            v = msg.get("voice")
            if v:
                voices.append(str(v))
            if "speed" in msg:
                try:
                    speeds.append(float(msg.get("speed")))
                except Exception:
                    pass
            if "pitch" in msg:
                try:
                    pitches.append(float(msg.get("pitch")))
                except Exception:
                    pass
            ap = msg.get("audio_path")
            if ap:
                audio_count += 1
                try:
                    p = Path(ap)
                    if p.exists():
                        total_audio_bytes += p.stat().st_size
                except Exception:
                    pass

        header = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "title": req.title or req.session_id,
            "session_id": req.session_id,
            "format_requested": req.format,
            "rounds_completed": req.content.rounds_completed,
            "agent_order": req.content.agent_order,
            "counts": {
                "turns": len(convo),
                "unique_speakers": len(set(speakers)),
                "audio_items": audio_count,
            },
            "voices_used": sorted(list(set(voices))) if voices else [],
            "speed_stats": {
                "min": min(speeds) if speeds else None,
                "max": max(speeds) if speeds else None,
                "avg": (sum(speeds) / len(speeds)) if speeds else None,
            },
            "pitch_stats": {
                "min": min(pitches) if pitches else None,
                "max": max(pitches) if pitches else None,
                "avg": (sum(pitches) / len(pitches)) if pitches else None,
            },
            "total_audio_bytes": total_audio_bytes,
        }
        summary = {"header": header, "request": json.loads(req.json())}
        summary_path = out_dir / "summary.json"
        _write_json(summary_path, summary)

        # 2) transcript.txt (speaker-labeled)
        transcript_lines = _mk_transcript_lines(
            convo=convo,
            include_ts=req.include_timestamps,
            include_audio=req.include_audio_links,
        )
        transcript_text = "\n".join(transcript_lines)
        transcript_path = out_dir / "transcript.txt"
        _write_text(transcript_path, transcript_text)

        # 3) final_script.txt (cleaner podcast script)
        script_text = _mk_script(convo)
        script_path = out_dir / "final_script.txt"
        _write_text(script_path, script_text)

        # 4) index.html (pretty view)
        html_text = _mk_html(topic=req.content.topic, convo=convo,
                             include_ts=req.include_timestamps,
                             include_audio=req.include_audio_links)
        html_path = out_dir / "index.html"
        _write_text(html_path, html_text)

        # 5) Optional: index.pdf (if reportlab present or explicitly requested as format)
        pdf_path = None
        if fmt == "pdf" or REPORTLAB_AVAILABLE:
            try:
                pdf_path = out_dir / "index.pdf"
                _write_pdf_from_text(pdf_path, req.title or req.session_id, html_text)
            except Exception as _e:
                logger.warning(f"PDF generation skipped: {_e}")

        files = {
            "summary.json": str(summary_path),
            "transcript.txt": str(transcript_path),
            "final_script.txt": str(script_path),
            "index.html": str(html_path),
        }
        if pdf_path and Path(pdf_path).exists():
            files["index.pdf"] = str(pdf_path)

        zip_path = None
        if fmt == "zip":
            zip_path = out_dir.with_suffix(".zip")
            _zip_dir(out_dir, zip_path)

        elapsed = round(time.time() - t0, 3)
        return ExportResponse(
            status="ok",
            session_id=req.session_id,
            title=safe_title,
            dir=str(out_dir),
            files=files,
            zip_path=(str(zip_path) if zip_path else None),
            processing_time=elapsed,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ export failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ========= Convenience: download endpoints =========
@app.get("/download/file")
async def download_file(path: str):
    """Serve a single file by absolute path within EXPORT_DIR."""
    try:
        p = Path(path).resolve()
        if not str(p).startswith(str(EXPORT_ROOT)):
            raise HTTPException(status_code=400, detail="Invalid path")
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(p))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/download/zip")
async def download_zip(title_or_session: str):
    """Serve the ZIP matching /exports/<title>_<timestamp>.zip if present."""
    try:
        # Find the newest matching folder and zip
        candidates = sorted(EXPORT_ROOT.glob(f"{sanitize_filename(title_or_session)}_*"), reverse=True)
        if not candidates:
            raise HTTPException(status_code=404, detail="No export directory found")
        z = candidates[0].with_suffix(".zip")
        if not z.exists():
            raise HTTPException(status_code=404, detail="ZIP not found")
        return FileResponse(str(z), media_type="application/zip", filename=z.name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ========= Optional: list exports (debug/ops) =========
@app.get("/list")
async def list_exports():
    if not settings.ALLOW_LISTING:
        raise HTTPException(status_code=403, detail="Listing disabled")
    items: List[Dict[str, Any]] = []
    for d in sorted(EXPORT_ROOT.glob("*"), reverse=True):
        if d.is_dir():
            z = d.with_suffix(".zip")
            items.append({
                "dir": str(d),
                "zip": str(z) if z.exists() else None,
                "files": [str(p) for p in d.glob("*") if p.is_file()]
            })
    return {"count": len(items), "exports": items}

# ========= Health & Root =========
@app.get("/health")
async def health():
    try:
        _ = list(EXPORT_ROOT.glob("*"))
        return {
            "status": "healthy",
            "service": "Agent4 - Exporter",
            "version": "2.2.1",
            "export_root": str(EXPORT_ROOT),
            "listing_enabled": settings.ALLOW_LISTING
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

@app.get("/")
async def root():
    return {
        "service": "Agent4 - Exporter",
        "version": "2.2.1",
        "endpoints": {
            "export": "POST /export",
            "download_file": "GET /download/file?path=...",
            "download_zip": "GET /download/zip?title_or_session=...",
            "list": "GET /list",
            "health": "GET /health",
        },
        "notes": [
            "Write-once export directory per request",
            "ZIP optional (default), JSON/TXT/HTML always generated",
            "Traversal-safe download endpoints",
        ],
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent4:app", host="0.0.0.0", port=8006, log_level="info", reload=False)
