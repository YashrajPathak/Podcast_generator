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

# Load environment
load_dotenv()
GRAPH_BASE = os.getenv("GRAPH_BASE", "http://localhost:8008")
AGENT1_URL = os.getenv("AGENT1_URL", "http://localhost:8001")
AGENT4_URL = os.getenv("AGENT4_URL", "http://localhost:8006")
AGENT5_URL = os.getenv("AGENT5_URL", "http://localhost:8007")

# API Endpoints
GRAPH_SESSIONS = f"{GRAPH_BASE}/sessions"
GRAPH_RUN = f"{GRAPH_BASE}/run"
GRAPH_STATE = f"{GRAPH_BASE}/session"  # + /{session_id}/state
GRAPH_MUTE = f"{GRAPH_BASE}/session"   # + /{session_id}/mute?agent=...
GRAPH_VOICES = f"{GRAPH_BASE}/session" # + /{session_id}/voices?agent=..&voice=..
GRAPH_SET_AGENTS = f"{GRAPH_BASE}/session"  # + /{session_id}/agents
GRAPH_USER_TURN = f"{GRAPH_BASE}/conversation/user-turn"
GRAPH_EXPORT = f"{GRAPH_BASE}/session"  # + /{session_id}/export?format=...

# WebRTC config
RTC_CONFIG = RTCConfiguration(
    {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
)

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
        "active_tab": "Control Room"
    }
    
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()
st.set_page_config(page_title="Podcast Control Room", page_icon="🎙", layout="wide")

# --- Helper Functions ---
def get_sessions():
    try:
        r = httpx.get(GRAPH_SESSIONS, timeout=5)
        r.raise_for_status()
        data = r.json()
        st.session_state.registry_keys = data.get("registry_keys", st.session_state.registry_keys)
        return data.get("sessions", [])
    except Exception as e:
        st.error(f"Failed to get sessions: {e}")
        return []

def get_state(session_id: str):
    try:
        r = httpx.get(f"{GRAPH_STATE}/{session_id}/state", timeout=5)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to get state: {e}")
        return None

def post_run(event: str, input_text: str = None, agents=None, voices=None, max_turns=None):
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
        
    try:
        r = httpx.post(GRAPH_RUN, json=payload, timeout=10)
        r.raise_for_status()
        st.success(f"Action completed: {event}")
        return r.json()
    except Exception as e:
        st.error(f"Failed to execute {event}: {e}")
        return None

def toggle_mute(agent: str, mute: bool):
    try:
        action = "mute" if mute else "unmute"
        r = httpx.post(f"{GRAPH_MUTE}/{st.session_state.session_id}/{action}?agent={agent}", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to toggle mute: {e}")
        return None

def set_voice(agent: str, voice: str):
    try:
        r = httpx.post(
            f"{GRAPH_VOICES}/{st.session_state.session_id}/voices?agent={agent}&voice={voice}", 
            timeout=5
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to set voice: {e}")
        return None

def set_agents(agents: list):
    try:
        r = httpx.post(
            f"{GRAPH_SET_AGENTS}/{st.session_state.session_id}/agents", 
            json=agents, 
            timeout=5
        )
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
        r = httpx.post(GRAPH_USER_TURN, json=payload, timeout=10)
        r.raise_for_status()
        st.success("User turn sent to panel!")
        return r.json()
    except Exception as e:
        st.error(f"Failed to send user turn: {e}")
        return None

def export_session(format: str = "html"):
    try:
        r = httpx.post(
            f"{GRAPH_EXPORT}/{st.session_state.session_id}/export?format={format}", 
            timeout=30
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Export failed: {e}")
        return None

def get_metrics():
    try:
        r = httpx.get(f"{AGENT5_URL}/metrics", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to get metrics: {e}")
        return None

def send_audio_to_agent1(bytes_wav: bytes, filename: str):
    files = {"file": (filename, bytes_wav, "audio/wav")}
    data = {"input_type": "audio", "session_id": st.session_state.session_id}
    
    try:
        r = httpx.post(f"{AGENT1_URL}/v1/input", files=files, data=data, timeout=20)
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
        
        col1, col2 = st.columns(2)
        if col1.button("▶️ Start", use_container_width=True):
            post_run("start", agents=st.session_state.picked_agents)
        if col2.button("⏹ End", use_container_width=True):
            post_run("end")
    
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
                r = httpx.post(f"{GRAPH_BASE}/registry/reload", timeout=5)
                r.raise_for_status()
                st.success("Registry reloaded!")
            except Exception as e:
                st.error(f"Reload failed: {e}")

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
        if st.button("Send Message", key="send_message"):
            if message.strip():
                send_user_turn(message.strip())
    
    with tab3:
        interrupt = st.text_area("Interruption", height=100)
        if st.button("Interrupt Panel", key="send_interrupt"):
            if interrupt.strip():
                send_user_turn(interrupt.strip(), is_interruption=True)
    
    with tab4:
        st.warning("Audio input requires additional setup")
        audio_file = st.file_uploader("Upload audio", type=["wav", "mp3"])
        if audio_file and st.button("Process Audio"):
            result = send_audio_to_agent1(audio_file.getvalue(), audio_file.name)
            if result:
                st.success(f"Audio processed: {result.get('response')}")

def render_agent_status():
    st.subheader("👥 Agent Status")
    state = get_state(st.session_state.session_id)
    
    if not state:
        st.info("No active session")
        return
        
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
    st.subheader("📤 Export Session")
    col1, col2 = st.columns([1, 2])
    format = col1.selectbox("Format", ["html", "pdf", "json", "txt"])
    
    if col2.button("Generate Export"):
        result = export_session(format)
        if result and "download_url" in result:
            st.success("Export created successfully!")
            st.markdown(f"**[Download Export]({result['download_url']})**")
        elif result:
            st.download_button(
                label="Download Export",
                data=json.dumps(result, indent=2),
                file_name=f"podcast_export.{format}",
                mime="application/json" if format=="json" else "text/plain"
            )

def render_metrics():
    st.subheader("📊 System Metrics")
    if st.button("Refresh Metrics"):
        st.session_state.last_metrics = get_metrics()
    
    if not st.session_state.last_metrics:
        st.info("No metrics available")
        return
        
    metrics = st.session_state.last_metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("Uptime", f"{metrics.get('uptime_sec', 0)}s")
    col2.metric("Sessions", metrics.get("session_count", 0))
    col3.metric("Avg Latency", f"{metrics.get('avg_latency', 0):.2f}ms")
    
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
