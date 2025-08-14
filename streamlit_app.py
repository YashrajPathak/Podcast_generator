# podcast_control_room.py - Unified Podcast Studio
import os
import time
import json
import asyncio
import httpx
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv
from streamlit_webrtc import webrtc_streamer, WebRtcMode, RTCConfiguration
import queue
from pathlib import Path
import io
import csv
import numpy as np
import wave

# Load environment
load_dotenv()
GRAPH_BASE = os.getenv("GRAPH_BASE", "http://localhost:8008")
AGENT1_URL = os.getenv("AGENT1_URL", "http://localhost:8001")
AGENT4_URL = os.getenv("AGENT4_URL", "http://localhost:8006")
AGENT5_URL = os.getenv("AGENT5_URL", "http://localhost:8007")

# Mic thresholds (env-overridable)
MIC_MIN_SECONDS = float(os.getenv("MIC_MIN_SECONDS", "0.4"))
MIC_MIN_LEVEL = int(os.getenv("MIC_MIN_LEVEL", "200"))  # avg |int16| level to treat as not-silent

# API Endpoints
GRAPH_SESSIONS = f"{GRAPH_BASE}/sessions"
GRAPH_RUN = f"{GRAPH_BASE}/run"
GRAPH_STATE = f"{GRAPH_BASE}/session"  # + /{session_id}/state
GRAPH_MUTE = f"{GRAPH_BASE}/session"   # + /{session_id}/mute?agent=...
GRAPH_VOICES = f"{GRAPH_BASE}/session" # + /{session_id}/voices?agent=..&voice=..
GRAPH_SET_AGENTS = f"{GRAPH_BASE}/session"  # + /{session_id}/agents
GRAPH_USER_TURN = f"{GRAPH_BASE}/conversation/user-turn"
GRAPH_EXPORT = f"{GRAPH_BASE}/session"  # + /{session_id}/export?format=...
GRAPH_REGISTRY = f"{GRAPH_BASE}/registry"

# WebRTC config
RTC_CONFIG = RTCConfiguration(
    {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
)
RTC_CFG = RTCConfiguration({
    "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]
})

# Initialize session state
def init_session_state():
    defaults = {
        "session_id": f"podcast_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "auto_refresh": True,
        "last_state": None,
        "registry_keys": ["agent1", "agent2", "agent3", "agent4", "agent5"],
        "picked_agents": ["agent1", "agent2", "agent3"],
        "voices": {
            "agent1": "en-US-AriaNeural",
            "agent2": "en-GB-RyanNeural",
            "agent3": "en-IN-PrabhatNeural",
            "agent4": "en-US-GuyNeural",
            "agent5": "en-US-JennyNeural"
        },
        "audio_ctx": None,
        "audio_queue": queue.Queue(),
        "last_metrics": None,
        "active_tab": "Control Room",
        "is_running": False,
        "export_done": False,
        "target_minutes": 0,
        # UI debounce locks
        "run_busy": False,
        "export_busy": False,
        "registry": None,
    }
    
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()
st.set_page_config(page_title="Podcast Control Room", page_icon="🎙", layout="wide")

# --- Helper Functions ---
def _with_retries(send, should_retry=None, max_retries=None, base_delay=None, verbose_env_var: str = "VERBOSE_RETRY"):
    """Generic retry wrapper for httpx calls.

    Args:
        send: zero-arg callable that returns an httpx.Response
        should_retry: optional callable(response) -> bool to signal retry despite 2xx-4xx
        max_retries: optional int; falls back to env MAX_RETRIES or 3
        base_delay: optional float seconds; falls back to env RETRY_BASE_DELAY or 0.5
        verbose_env_var: env var name; when truthy, emits st.info about attempts
    Returns:
        httpx.Response
    Raises:
        Last exception if all retries exhausted, or RuntimeError signaled by should_retry
    """
    import os, time, random
    mr = int(max_retries if max_retries is not None else os.getenv("MAX_RETRIES", 3))
    bd = float(base_delay if base_delay is not None else os.getenv("RETRY_BASE_DELAY", 0.5))
    verbose = os.getenv(verbose_env_var, "").lower() in {"1", "true", "yes", "on"}
    last_err = None
    for attempt in range(mr):
        try:
            resp = send()
            # default transient retry: 5xx
            transient = getattr(resp, "status_code", 500) >= 500
            if should_retry is not None:
                transient = bool(should_retry(resp)) or transient
            if transient:
                raise RuntimeError(f"Transient HTTP {resp.status_code}")
            return resp
        except Exception as e:
            last_err = e
            if attempt >= mr - 1:
                break
            # jittered exponential backoff
            delay = min(8.0, bd * (2 ** attempt)) * (0.8 + 0.4 * random.random())
            if verbose:
                try:
                    st.info(f"Retrying ({attempt+1}/{mr-1}) in {delay:.2f}s: {e}")
                except Exception:
                    pass
            time.sleep(delay)
    raise last_err

def get_sessions():
    try:
        r = _with_retries(lambda: httpx.get(GRAPH_SESSIONS, timeout=5))
        r.raise_for_status()
        data = r.json()
        st.session_state.registry_keys = data.get("registry_keys", st.session_state.registry_keys)
        return data.get("sessions", [])
    except Exception as e:
        st.error(f"Failed to get sessions: {e}")
        return []

def get_state(session_id: str):
    try:
        url = f"{GRAPH_STATE}/{session_id}/state"
        # Do not retry 404 (treated as missing session)
        r = _with_retries(lambda: httpx.get(url, timeout=5), should_retry=lambda resp: resp.status_code >= 500)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to get state: {e}")
        return None

def post_run(event: str, input_text: str = None, agents=None, voices=None, max_turns=None, target_minutes: int = None):
    payload = {
        "session_id": st.session_state.session_id,
        "event": event
    }
    if input_text: 
        payload["input"] = input_text
    if agents: 
        payload["agents"] = agents
    if voices: 
        payload["voices"] = voices
    if max_turns is not None: 
        payload["max_turns"] = max_turns
    if target_minutes is not None:
        payload["target_minutes"] = int(target_minutes)
        
    # Debounce: lock while request in-flight
    if st.session_state.run_busy:
        st.warning("Action already in progress…")
        return None
    st.session_state.run_busy = True
    try:
        r = _with_retries(lambda: httpx.post(GRAPH_RUN, json=payload, timeout=10))
        r.raise_for_status()
        st.success(f"Action completed: {event}")
        if event == "start":
            st.session_state.is_running = True
            st.session_state.export_done = False
            if target_minutes is not None:
                st.session_state.target_minutes = int(target_minutes)
        if event == "end":
            st.session_state.is_running = False
        return r.json()
    except Exception as e:
        st.error(f"Failed to execute {event}: {e}")
        return None
    finally:
        st.session_state.run_busy = False

def toggle_mute(agent: str, mute: bool):
    try:
        action = "mute" if mute else "unmute"
        url = f"{GRAPH_MUTE}/{st.session_state.session_id}/{action}?agent={agent}"
        r = _with_retries(lambda: httpx.post(url, timeout=5))
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to toggle mute: {e}")
        return None

def set_voice(agent: str, voice: str):
    try:
        url = f"{GRAPH_VOICES}/{st.session_state.session_id}/voices?agent={agent}&voice={voice}"
        r = _with_retries(lambda: httpx.post(url, timeout=5))
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to set voice: {e}")
        return None

def set_agents(agents: list):
    try:
        url = f"{GRAPH_SET_AGENTS}/{st.session_state.session_id}/agents"
        r = _with_retries(lambda: httpx.post(url, json=agents, timeout=5))
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to set agents: {e}")
        return None

def send_user_turn(text: str, is_interruption=False):
    try:
        payload = {
            "session_id": st.session_state.session_id,
            "text": text,
            "is_interruption": is_interruption
        }
        r = _with_retries(lambda: httpx.post(GRAPH_USER_TURN, json=payload, timeout=10))
        r.raise_for_status()
        st.success("User turn sent to panel!")
        return r.json()
    except Exception as e:
        st.error(f"Failed to send user turn: {e}")
        return None

def export_session(format: str = "html"):
    try:
        url = f"{GRAPH_EXPORT}/{st.session_state.session_id}/export?format={format}"
        r = _with_retries(lambda: httpx.post(url, timeout=30))
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Export failed: {e}")
        return None

def get_metrics():
    try:
        url = f"{AGENT5_URL}/metrics"
        r = _with_retries(lambda: httpx.get(url, timeout=5))
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to get metrics: {e}")
        return None

def get_registry():
    """Fetch centralized registry from Graph."""
    try:
        r = _with_retries(lambda: httpx.get(GRAPH_REGISTRY, timeout=10))
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.warning(f"Failed to fetch registry: {e}")
        return None

def send_audio_to_agent1(bytes_wav: bytes, filename: str):
    files = {"file": (filename, bytes_wav, "audio/wav")}
    data = {"session_id": st.session_state.session_id}
    
    try:
        url = f"{AGENT1_URL}/conversation/user-audio"
        r = _with_retries(lambda: httpx.post(url, files=files, data=data, timeout=30))
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Audio processing failed: {e}")
        return None

# --- UI Components ---
def render_sidebar():
    st.sidebar.title("🎛 Control Panel")
    
    # Session Management
    with st.sidebar.expander("📂 Sessions", expanded=True):
        sessions = get_sessions()
        existing_ids = [s["session_id"] for s in sessions] if sessions else []
        pick = st.selectbox("Load session", ["(new)"] + existing_ids)
        if pick != "(new)":
            st.session_state.session_id = pick
        
        st.session_state.session_id = st.text_input(
            "Session ID", 
            value=st.session_state.session_id
        )
        
        target = st.select_slider("Target duration (min)", options=[0,1,2,3,4,5,6,7,8,9,10], value=st.session_state.get("target_minutes", 3), help="0 = legacy (no duration cap)")
        
        col1, col2 = st.columns(2)
        if col1.button("▶️ Start", use_container_width=True, disabled=st.session_state.is_running or st.session_state.run_busy):
            post_run("start", agents=st.session_state.picked_agents, target_minutes=target)
        if col2.button("⏹ End", use_container_width=True, disabled=not st.session_state.is_running or st.session_state.run_busy):
            post_run("end")
    
        # Stop & Export convenience
        if st.sidebar.button("⏹➡️ Stop & Export", use_container_width=True, disabled=not st.session_state.is_running or st.session_state.run_busy or st.session_state.export_busy):
            post_run("end")
            # export is handled in Export tab guarded by export_done
    
    # Agent Configuration
    with st.sidebar.expander("🧑‍🤝‍🧑 Agents", expanded=True):
        st.session_state.picked_agents = st.multiselect(
            "Active Agents",
            options=st.session_state.registry_keys,
            default=st.session_state.picked_agents
        )
        
        if st.button("Update Panel", use_container_width=True):
            set_agents(st.session_state.picked_agents)
        
        st.divider()
        
        for agent in st.session_state.picked_agents:
            col1, col2 = st.columns([3, 1])
            new_voice = col1.text_input(
                f"{agent} voice", 
                value=st.session_state.voices.get(agent, ""),
                key=f"voice_{agent}"
            )
            if col2.button("Set", key=f"set_{agent}"):
                set_voice(agent, new_voice)
                st.session_state.voices[agent] = new_voice
            
            col3, col4 = st.columns(2)
            if col3.button(f"🔇 Mute", key=f"mute_{agent}"):
                toggle_mute(agent, True)
            if col4.button(f"🔊 Unmute", key=f"unmute_{agent}"):
                toggle_mute(agent, False)
    
    # System Controls
    with st.sidebar.expander("⚙️ System", expanded=True):
        st.session_state.auto_refresh = st.checkbox(
            "🔄 Auto-refresh", 
            value=st.session_state.auto_refresh
        )
        
        if st.button("🔄 Reload Registry", use_container_width=True):
            try:
                r = _with_retries(lambda: httpx.post(f"{GRAPH_BASE}/registry/reload", timeout=5))
                r.raise_for_status()
                st.success("Registry reloaded!")
            except Exception as e:
                st.error(f"Reload failed: {e}")

    # Registry quick-view
    if st.session_state.get("registry") is None:
        st.session_state.registry = get_registry()
    if st.sidebar.button("Load Registry"):
        st.session_state.registry = get_registry()
    if st.session_state.get("registry"):
        reg = st.session_state.registry
        st.sidebar.caption(f"Registry: {reg.get('count', 0)} agents")
        try:
            st.sidebar.json(reg.get("agents", {}), expanded=False)
        except Exception:
            pass

def render_audio_player():
    st.subheader("🔊 Live Audio Stream")
    audio_ctx = webrtc_streamer(
        key="podcast-audio",
        mode=WebRtcMode.RECVONLY,
        rtc_configuration=RTC_CONFIG,
        media_stream_constraints={"audio": True},
        audio_receiver_size=1024,
    )
    st.session_state.audio_ctx = audio_ctx

def render_input_panel():
    st.subheader("💬 Drive Conversation")
    tab1, tab2, tab3, tab4 = st.tabs(["Topic", "Message", "Interrupt", "Audio"])
    
    with tab1:
        topic = st.text_area("Discussion Topic", height=100)
        if st.button("Set Topic", key="set_topic"):
            if topic.strip():
                post_run("topic_input", input_text=topic.strip())
    
    with tab2:
        message = st.text_area("Your Message", height=100)
        c1, c2 = st.columns([1,1])
        if c1.button("Send Message", key="send_message"):
            if message.strip():
                send_user_turn(message.strip())
        if c2.button("Stop Session", key="stop_session"):
            post_run("end")
    
    with tab3:
        interrupt = st.text_area("Interruption", height=100)
        if st.button("Interrupt Panel", key="send_interrupt"):
            if interrupt.strip():
                send_user_turn(interrupt.strip(), is_interruption=True)
    
    with tab4:
        st.caption("Upload a short WAV/MP3 mic clip to interrupt the panel.")
        audio_file = st.file_uploader("Upload audio", type=["wav", "mp3"])
        if audio_file and st.button("Process Audio"):
            result = send_audio_to_agent1(audio_file.getvalue(), audio_file.name)
            if result:
                st.success(f"Mic interrupt sent. Transcript: {result.get('transcript','(n/a)')}")

        st.divider()
        st.caption("Experimental: Live mic via WebRTC → WAV upload")
        ctx = webrtc_streamer(
            key="mic_webrtc",
            mode=WebRtcMode.SENDONLY,
            audio_receiver_size=1024,
            rtc_configuration=RTC_CFG,
            media_stream_constraints={"audio": True, "video": False},
        )

        # Permission/availability guidance
        if not ctx:
            st.warning("Mic capture not initialized. Check browser support and refresh.")
        elif not ctx.state.playing:
            st.info("Click 'Allow' when prompted to enable microphone access.")

        colw1, colw2 = st.columns(2)
        if colw1.button("Clear Mic Buffer", key="clear_webrtc") and ctx and ctx.state.playing:
            try:
                if ctx.audio_receiver:
                    while True:
                        ctx.audio_receiver.get_frame(timeout=0.01)
            except queue.Empty:
                pass

        if colw2.button("Send WebRTC Audio", key="send_webrtc") and ctx and ctx.state.playing:
            pcm_chunks = []
            sample_rate = 24000
            got_any = False
            try:
                if not ctx.audio_receiver:
                    st.error("No audio receiver active")
                else:
                    while True:
                        try:
                            frame = ctx.audio_receiver.get_frame(timeout=0.01)
                        except queue.Empty:
                            break
                        if frame is None:
                            break
                        # Convert to mono int16, preserve frame sample_rate
                        try:
                            arr = frame.to_ndarray(format="s16", layout="mono")  # shape: (samples,)
                            pcm_chunks.append(arr.tobytes())
                            sample_rate = frame.sample_rate or sample_rate
                            got_any = True
                        except Exception:
                            pass
            except Exception as e:
                st.error(f"Capture failed: {e}")

            if got_any:
                # Debounce by duration and simple energy check
                try:
                    total_samples = sum(len(c) for c in pcm_chunks) // 2  # int16
                    duration = total_samples / float(sample_rate or 24000)
                    if duration < MIC_MIN_SECONDS:
                        st.warning(f"Captured clip too short ({duration:.2f}s < {MIC_MIN_SECONDS:.2f}s). Try again.")
                        return
                    import numpy as _np
                    joined = b"".join(pcm_chunks)
                    levels = _np.frombuffer(joined, dtype=_np.int16)
                    mean_level = float(_np.mean(_np.abs(levels))) if levels.size else 0.0
                    if mean_level < MIC_MIN_LEVEL:
                        st.warning("Detected near-silence. Speak louder/closer and try again.")
                        return
                except Exception:
                    # Non-fatal analysis failure; continue
                    pass
                # Wrap to WAV in-memory
                bio = io.BytesIO()
                try:
                    with wave.open(bio, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(int(sample_rate))
                        wf.writeframes(b"".join(pcm_chunks))
                    wav_bytes = bio.getvalue()
                    res = send_audio_to_agent1(wav_bytes, filename=f"webrtc_{int(time.time())}.wav")
                    if res:
                        st.success(f"WebRTC mic sent. Transcript: {res.get('transcript','(n/a)')}")
                except Exception as e:
                    st.error(f"WAV encode failed: {e}")

def render_agent_status():
    st.subheader("👥 Agent Status")
    state = get_state(st.session_state.session_id)
    
    if not state:
        st.info("No active session")
        return
        
    # Progress
    t_ms = int(state.get("target_ms") or 0)
    a_ms = int(state.get("audio_ms") or 0)
    if t_ms > 0:
        pct = max(0.0, min(1.0, a_ms / t_ms))
        st.progress(pct, text=f"Audio {a_ms/1000:.1f}s / {t_ms/1000:.1f}s")
        if state.get("ended"):
            st.success(f"Session ended: {state.get('ended_reason')}")
            st.session_state.is_running = False
            
            # Offer direct final WAV download if present and readable
            fw = state.get("final_wav")
            if fw and Path(fw).exists():
                try:
                    data = Path(fw).read_bytes()
                    st.download_button(
                        label="⬇️ Download Final WAV",
                        data=data,
                        file_name=Path(fw).name,
                        mime="audio/wav"
                    )
                except Exception:
                    pass
            
            # Build and offer transcript downloads (JSON and CSV)
            try:
                turn_audio = state.get("turn_audio", {}) or {}
                history = state.get("history", []) or []
                transcript = []
                for item in history:
                    turn_id = item.get("turn_id")
                    agent = item.get("agent") or item.get("speaker")
                    text = (item.get("text") or item.get("response") or "").strip()
                    ms = 0
                    if turn_id is not None and str(turn_id) in {str(k) for k in turn_audio.keys()}:
                        # turn_audio keys may be int or str; normalize lookup
                        info = turn_audio.get(turn_id) or turn_audio.get(str(turn_id)) or {}
                        ms = int(info.get("ms") or 0)
                    transcript.append({
                        "turn_id": turn_id,
                        "speaker": agent,
                        "ms": ms,
                        "text": text,
                    })

                # JSON
                json_bytes = json.dumps({
                    "session_id": state.get("session_id"),
                    "target_ms": state.get("target_ms"),
                    "audio_ms": state.get("audio_ms"),
                    "ended_reason": state.get("ended_reason"),
                    "turns": transcript,
                }, ensure_ascii=False, indent=2).encode("utf-8")
                st.download_button(
                    label="⬇️ Download Transcript (JSON)",
                    data=json_bytes,
                    file_name=f"{state.get('session_id','session')}_transcript.json",
                    mime="application/json"
                )

                # CSV
                csv_buf = io.StringIO()
                writer = csv.DictWriter(csv_buf, fieldnames=["turn_id", "speaker", "ms", "text"])
                writer.writeheader()
                for row in transcript:
                    writer.writerow(row)
                st.download_button(
                    label="⬇️ Download Transcript (CSV)",
                    data=csv_buf.getvalue().encode("utf-8"),
                    file_name=f"{state.get('session_id','session')}_transcript.csv",
                    mime="text/csv"
                )
            except Exception:
                pass
    
    cols = st.columns(len(st.session_state.picked_agents))
    for i, agent in enumerate(st.session_state.picked_agents):
        muted = state.get("mute", {}).get(agent, False)
        cols[i].metric(
            f"Agent {agent}",
            "🔇 Muted" if muted else "🔊 Active",
            "Speaking" if agent in state.get("current_speakers", {}) else "Idle"
        )

def render_conversation():
    st.subheader("💬 Live Transcript")
    state = get_state(st.session_state.session_id)
    
    if not state or not state.get("history"):
        st.info("No conversation history yet")
        return
        
    for turn in reversed(state["history"]):
        with st.container():
            timestamp = datetime.fromtimestamp(turn["timestamp"]).strftime("%H:%M:%S")
            st.markdown(f"**{turn['agent']}** · `{timestamp}`")
            st.write(turn["response"])
            st.divider()

def render_export():
    st.subheader("📦 Export")
    fmt = st.selectbox("Format", ["zip", "json"], index=0)
    ended = False
    try:
        s = get_state(st.session_state.session_id)
        ended = bool(s and s.get("ended"))
    except Exception:
        pass
    auto_hint = "(auto-allowed after session end)" if ended else ""
    # Debounce export
    disabled_now = st.session_state.export_busy or (st.session_state.export_done and ended)
    if st.button(f"Generate Export Bundle {auto_hint}", disabled=disabled_now):
        if st.session_state.export_busy:
            st.info("Export already in progress…")
        else:
            st.session_state.export_busy = True
            try:
                r = _with_retries(lambda: httpx.get(f"{GRAPH_EXPORT}/{st.session_state.session_id}/export", params={"format": fmt}, timeout=30))
                r.raise_for_status()
                data = r.json()
                st.session_state.export_done = True
                sid = st.session_state.session_id
                st.success(f"Export complete for session {sid} (format: {fmt})")
                st.json(data)
                if data.get("download_url"):
                    suggested = f"podcast_{sid}.{fmt}"
                    st.markdown(f"[⬇️ Download export]({data['download_url']})  ")
                    st.caption(f"Suggested filename: {suggested}")
                # Always provide a reliable ZIP link when format is zip, regardless of download_url presence
                try:
                    if fmt == "zip":
                        # Prefer Agent4 base from Graph registry
                        a4_base = None
                        reg = st.session_state.get("registry")
                        if reg and isinstance(reg.get("agents"), dict):
                            a4 = reg["agents"].get("agent4") or {}
                            a4_base = str(a4.get("url", "")).rstrip("/")
                        if not a4_base:
                            a4_base = AGENT4_URL.rstrip("/")
                        zip_url = f"{a4_base}/download/zip?title_or_session=Podcast_{sid}"
                        st.markdown(f"[🗜️ Download ZIP]({zip_url})")
                except Exception:
                    pass
                # Show final.wav download if present in files map
                try:
                    files = data.get("files", {}) or {}
                    fin = files.get("final.wav")
                    if fin:
                        # Prefer registry Agent4 URL if available
                        a4_base = None
                        reg = st.session_state.get("registry")
                        if reg and isinstance(reg.get("agents"), dict):
                            a4 = reg["agents"].get("agent4") or {}
                            a4_base = str(a4.get("url", "")).rstrip("/")
                        if not a4_base:
                            a4_base = AGENT4_URL.rstrip("/")
                        dl = f"{a4_base}/download/file?path={fin}"
                        st.markdown(f"[🎵 Download final.wav]({dl})")
                except Exception as _e:
                    st.caption("final.wav link unavailable")
            except Exception as e:
                st.error(f"Export failed: {e}")
            finally:
                st.session_state.export_busy = False
    # Optional explicit re-export
    if ended and st.session_state.export_done:
        if st.button("Re-export", disabled=st.session_state.export_busy):
            st.session_state.export_done = False
            st.rerun()

def render_metrics():
    st.subheader("📊 System Metrics")
    if st.button("Refresh Metrics"):
        st.session_state.last_metrics = get_metrics()
    
    if not st.session_state.last_metrics:
        st.info("No metrics available")
        return
        
    metrics = st.session_state.last_metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("Sessions", metrics.get("sessions", 0))
    col2.metric("Poll Interval", f"{metrics.get('poll_interval', 0)}s")
    col3.metric("Retention Days", metrics.get("retention_days", 0))
    st.code(metrics.get("db_path", ""), language="text")
    st.caption(f"Metrics time: {metrics.get('time', 0)}")
    st.json(metrics, expanded=False)

def render_control_room():
    col1, col2 = st.columns([3, 1])
    col1.title("🎙 Podcast Control Room")
    col2.metric("Current Session", st.session_state.session_id)
    
    render_audio_player()
    render_input_panel()
    render_agent_status()
    render_conversation()

# --- Main App ---
st.sidebar.title("Navigation")
st.session_state.active_tab = st.sidebar.radio(
    "Go to",
    ["Control Room", "Export", "Metrics"],
    index=0
)

render_sidebar()

if st.session_state.active_tab == "Control Room":
    render_control_room()
elif st.session_state.active_tab == "Export":
    render_export()
elif st.session_state.active_tab == "Metrics":
    render_metrics()

# Auto-refresh logic
if st.session_state.auto_refresh:
    time.sleep(2)
    st.rerun()
