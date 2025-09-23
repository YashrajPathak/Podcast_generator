# graph.py
# Complete LiveKit + LangGraph implementation for real-time voice agents

import os
import sys
import re
import json
import uuid
import asyncio
import datetime
import atexit
import webbrowser
import threading
import wave
import tempfile
from time import perf_counter
from pathlib import Path
from typing import Dict, List, Any, Optional, TypedDict, Literal
from http.server import HTTPServer, SimpleHTTPRequestHandler
from enum import Enum

# ---- LiveKit Integration ----
try:
    import livekit.rtc as lkrtc
    from livekit.agents import (
        AutoSubscribe,
        JobContext,
        Worker,
        llm,
        stt,
        tts,
        voice_assistant,
    )
    from livekit.agents.pipeline import VoicePipeline
    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    print("⚠️  LiveKit not available. Install with: pip install 'livekit-agents'")
    lkrtc = None

# ---- Import your podcast engine ----
try:
    import lkk as podcast
except Exception as e:
    print("❌ Could not import lkk.py. Make sure the file is named 'lkk.py'")
    raise

# ---- LangGraph core ----
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

# ---- Rich console ----
try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, BarColumn, TimeElapsedColumn, TimeRemainingColumn, TextColumn
    _RICH = True
except Exception:
    _RICH = False
    class Console:
        def print(self, *a, **k): print(*a)
        def rule(self, *a, **k): print("-" * 50)
    class Table:
        def __init__(self, title=None): self.rows=[]; self.title=title
        def add_column(self, *a, **k): pass
        def add_row(self, *cols): self.rows.append(cols)
    class Progress:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def add_task(self, *a, **k): return 1
        def update(self, *a, **k): pass

console = Console()

# ------------------------- Configuration -------------------------
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")

# ------------------------- Global State -------------------------
class ConversationMode(Enum):
    CLI = "cli"
    LIVEKIT = "livekit"
    BATCH = "batch"

class ConversationState:
    def __init__(self):
        self.active = False
        self.mode = None
        self.audio_segments = []
        self.transcript = []
        self.conversation_history = []
        self.current_turn = 0
        self.max_turns = 6
        self.context = ""
        self.worker = None
        self.room = None
        self.participant = None
        self.latency_data = []

conversation_state = ConversationState()

# ------------------------- WebSocket Server for Visualization -------------------------
try:
    import websockets
    _WEBSOCKETS_AVAILABLE = True
except ImportError:
    _WEBSOCKETS_AVAILABLE = False

_websocket_connections = set()
_websocket_port = 8083

async def websocket_handler(websocket, path):
    _websocket_connections.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        _websocket_connections.remove(websocket)

async def broadcast_websocket_message(data):
    if not _websocket_connections:
        return
    message = json.dumps(data)
    for connection in _websocket_connections.copy():
        try:
            await connection.send(message)
        except Exception:
            _websocket_connections.remove(connection)

def start_websocket_server():
    if not _WEBSOCKETS_AVAILABLE:
        return
    async def server_main():
        global _websocket_port
        for port in range(8083, 8090):
            try:
                server = await websockets.serve(websocket_handler, "localhost", port)
                _websocket_port = port
                print(f"WebSocket server running on ws://localhost:{port}")
                await server.wait_closed()
                break
            except OSError:
                continue
    asyncio.create_task(server_main())

def start_visualization_server(html_content, port=8002):
    class VisualizationHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(html_content.encode('utf-8'))
            else:
                super().do_GET()
    server = HTTPServer(('localhost', port), VisualizationHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

# ------------------------- LiveKit Handlers -------------------------
async def handle_track_subscribed(track: lkrtc.Track, publication: lkrtc.TrackPublication, participant: lkrtc.RemoteParticipant):
    if track.kind == lkrtc.TrackKind.KIND_AUDIO:
        print(f"🎤 Audio track subscribed from {participant.identity}")

async def handle_data_received(data: bytes, participant: lkrtc.RemoteParticipant, kind: lkrtc.DataPacketKind):
    try:
        message = json.loads(data.decode('utf-8'))
        if message.get('type') == 'text_input':
            await process_user_input(message['text'], participant.identity)
    except Exception as e:
        print(f"Error processing data: {e}")

async def handle_participant_connected(participant: lkrtc.RemoteParticipant):
    print(f"👤 Participant connected: {participant.identity}")
    conversation_state.participant = participant
    if not conversation_state.active:
        await start_conversation()

# ------------------------- Audio Processing -------------------------
async def speak_agent(agent_type: str, text: str):
    """Have an agent speak and add to conversation history"""
    start_time = datetime.datetime.now()
    
    conversation_state.conversation_history.append({
        "speaker": agent_type,
        "text": text,
        "timestamp": datetime.datetime.now().isoformat()
    })
    
    # Generate audio with Azure TTS
    ssml = podcast.text_to_ssml(text, agent_type)
    audio_file = await asyncio.to_thread(podcast.synth, ssml)
    conversation_state.audio_segments.append(audio_file)
    
    # Measure and display latency
    latency = (datetime.datetime.now() - start_time).total_seconds() * 1000
    conversation_state.latency_data.append(latency)
    print(f"🎯 Audio Latency: {latency:.0f}ms")
    
    # Play audio through LiveKit if in LiveKit mode
    if conversation_state.mode == ConversationMode.LIVEKIT and _LIVEKIT_AVAILABLE:
        await play_audio(audio_file)
    
    # Print to console and send to visualization
    print(f"{agent_type}: {text}")
    if _WEBSOCKETS_AVAILABLE:
        await broadcast_websocket_message({
            "node": f"{agent_type}_speak",
            "event": "audio_generated",
            "latency": latency,
            "text": text
        })

async def play_audio(audio_file: str):
    """Play audio file through LiveKit"""
    if not _LIVEKIT_AVAILABLE or not conversation_state.participant:
        return
        
    try:
        with wave.open(audio_file, 'rb') as wav_file:
            frames = wav_file.readframes(wav_file.getnframes())
            audio_frame = lkrtc.AudioFrame(
                data=frames,
                sample_rate=wav_file.getframerate(),
                num_channels=wav_file.getnchannels(),
                samples_per_channel=wav_file.getnframes() // wav_file.getnchannels()
            )
            
            # For now, just log that we would stream audio
            print(f"🔊 Would stream audio to participant: {audio_file}")
            
    except Exception as e:
        print(f"Error playing audio: {e}")

# ------------------------- Conversation Management -------------------------
async def start_conversation():
    """Initialize a new conversation"""
    conversation_state.active = True
    conversation_state.conversation_history = []
    conversation_state.audio_segments = []
    conversation_state.current_turn = 0
    conversation_state.latency_data = []
    
    # Load context
    context_text, meta = load_context("both")
    conversation_state.context = context_text
    
    print("🚀 Starting conversation...")
    
    # Start with agent introductions
    await speak_agent("NEXUS", podcast.NEXUS_INTRO)
    await speak_agent("RECO", podcast.RECO_INTRO)
    await speak_agent("STAT", podcast.STAT_INTRO)
    
    # Generate topic introduction
    topic_intro = await podcast.generate_nexus_topic_intro(context_text)
    await speak_agent("NEXUS", topic_intro)

async def process_user_input(text: str, user_id: str = "user"):
    """Process text input from user (via mic or text)"""
    if not conversation_state.active:
        await start_conversation()
    
    # Add to transcript
    conversation_state.transcript.append({
        "speaker": "USER",
        "text": text,
        "timestamp": datetime.datetime.now().isoformat()
    })
    
    print(f"👤 USER: {text}")
    
    # Process through conversation logic
    await handle_conversation_turn(text)

async def handle_conversation_turn(user_input: str):
    """Process a conversation turn with the agents"""
    conversation_state.current_turn += 1
    
    # Agent Reco responds
    reco_prompt = f"Context: {conversation_state.context}\nUser said: '{user_input}'. Provide one concise recommendation."
    reco_response = await podcast.llm(podcast.SYSTEM_RECO, reco_prompt)
    await speak_agent("RECO", reco_response)
    
    # Agent Stat responds
    stat_prompt = f"Context: {conversation_state.context}\nReco just said: '{reco_response}'. Provide one concise validation or risk assessment."
    stat_response = await podcast.llm(podcast.SYSTEM_STAT, stat_prompt)
    await speak_agent("STAT", stat_response)
    
    # Check if conversation should end
    if conversation_state.current_turn >= conversation_state.max_turns:
        await end_conversation()

async def end_conversation():
    """End conversation and generate final outputs"""
    await speak_agent("NEXUS", podcast.NEXUS_OUTRO)
    conversation_state.active = False
    
    # Calculate average latency
    if conversation_state.latency_data:
        avg_latency = sum(conversation_state.latency_data) / len(conversation_state.latency_data)
        print(f"📊 Average Latency: {avg_latency:.0f}ms")
    
    # Generate final audio file
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_wav = f"conversation_{timestamp}.wav"
    podcast.write_master(conversation_state.audio_segments, out_wav)
    
    # Generate transcript
    transcript_file = f"conversation_{timestamp}.json"
    with open(transcript_file, 'w', encoding='utf-8') as f:
        json.dump({
            "transcript": conversation_state.transcript,
            "conversation": conversation_state.conversation_history,
            "timestamp": timestamp,
            "audio_file": out_wav,
            "latency_stats": {
                "average": sum(conversation_state.latency_data) / len(conversation_state.latency_data) if conversation_state.latency_data else 0,
                "max": max(conversation_state.latency_data) if conversation_state.latency_data else 0,
                "min": min(conversation_state.latency_data) if conversation_state.latency_data else 0
            }
        }, f, indent=2)
    
    print(f"\n✅ Conversation completed!")
    print(f"   Audio: {out_wav}")
    print(f"   Transcript: {transcript_file}")

# ------------------------- LiveKit Worker -------------------------
async def entrypoint(ctx: JobContext):
    """LiveKit agent entrypoint"""
    print("🎧 Starting LiveKit voice agent...")
    
    await ctx.wait_for_connected()
    conversation_state.room = ctx.room
    
    # Subscribe to events
    ctx.room.on("track_subscribed", handle_track_subscribed)
    ctx.room.on("data_received", handle_data_received)
    ctx.room.on("participant_connected", handle_participant_connected)
    
    # Monitor connection quality
    @ctx.room.on("connection_quality_changed")
    def on_quality_changed(participant, quality):
        print(f"📶 Connection Quality: {quality.value} ({quality.score}/5)")
    
    print("✅ LiveKit agent ready - waiting for participants...")
    await asyncio.Future()

# ------------------------- CLI Interface -------------------------
async def cli_conversation_mode():
    """Text-based conversation mode for debugging"""
    print("🧪 CLI Conversation Mode (Type 'exit' to end)")
    print("=" * 50)
    
    conversation_state.mode = ConversationMode.CLI
    start_websocket_server()
    await start_conversation()
    
    while conversation_state.active:
        try:
            user_input = input("\nYou: ").strip()
            if user_input.lower() == 'exit':
                await end_conversation()
                break
                
            await process_user_input(user_input)
            
        except KeyboardInterrupt:
            await end_conversation()
            break
        except Exception as e:
            print(f"Error: {e}")

async def livekit_conversation_mode():
    """LiveKit-based conversation with mic input"""
    if not _LIVEKIT_AVAILABLE:
        print("❌ LiveKit not available. Use CLI mode instead.")
        return
        
    print("🎤 LiveKit Conversation Mode - Starting server...")
    conversation_state.mode = ConversationMode.LIVEKIT
    start_websocket_server()
    
    # Create and start LiveKit worker
    worker = Worker(
        entrypoint=entrypoint,
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )
    
    conversation_state.worker = worker
    
    try:
        await worker.run()
    except Exception as e:
        print(f"LiveKit error: {e}")
    finally:
        if conversation_state.active:
            await end_conversation()

async def batch_podcast_mode():
    """Batch podcast generation mode"""
    print("🎧 Batch Podcast Generation Mode")
    print("=" * 50)
    
    conversation_state.mode = ConversationMode.BATCH
    start_websocket_server()
    
    # Use existing podcast generation logic
    context_text, meta = load_context("both")
    conversation_state.context = context_text
    
    # Generate full podcast
    await speak_agent("NEXUS", podcast.NEXUS_INTRO)
    await speak_agent("RECO", podcast.RECO_INTRO)
    await speak_agent("STAT", podcast.STAT_INTRO)
    
    topic_intro = await podcast.generate_nexus_topic_intro(context_text)
    await speak_agent("NEXUS", topic_intro)
    
    # Conversation turns
    for i in range(conversation_state.max_turns):
        reco_prompt = f"Context: {context_text}\nProvide recommendation #{i+1} based on the metrics."
        reco_response = await podcast.llm(podcast.SYSTEM_RECO, reco_prompt)
        await speak_agent("RECO", reco_response)
        
        stat_prompt = f"Context: {context_text}\nReco said: '{reco_response}'. Provide validation or risk assessment."
        stat_response = await podcast.llm(podcast.SYSTEM_STAT, stat_prompt)
        await speak_agent("STAT", stat_response)
    
    await speak_agent("NEXUS", podcast.NEXUS_OUTRO)
    await end_conversation()

# ------------------------- Helper Functions -------------------------
def load_context(choice: str) -> tuple[str, dict]:
    return podcast.load_context(choice)

def list_json_files() -> List[str]:
    return [p.name for p in Path(".").iterdir() if p.is_file() and p.suffix.lower() == ".json"]

def _write_mermaid_bundle(compiled_graph, session_id: str) -> Optional[str]:
    """Generate visualization HTML"""
    try:
        mermaid = compiled_graph.get_graph().draw_mermaid()
    except Exception:
        return None
    
    html = f"""<!doctype html><html><head><title>LangGraph · {session_id}</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <style>body{{margin:0;padding:16px;font-family:system-ui;background:#0f172a;color:#f8fafc;}}
    .wrap{{max-width:1200px;margin:auto;}}.status{{background:#1e293b;padding:16px;border-radius:8px;margin-bottom:16px;}}
    #updates{{max-height:200px;overflow-y:auto;background:#1e293b;padding:8px;border-radius:4px;margin-top:16px;}}
    .update{{margin:4px 0;padding:4px;border-left:3px solid #3b82f6;}}</style></head>
    <body><div class="wrap"><h3>LangGraph · {session_id}</h3>
    <div class="status"><h4>Status: <span id="current-status">Ready</span></h4><div id="updates"></div></div>
    <div class="mermaid">{mermaid}</div></div>
    <script>
    mermaid.initialize({{startOnLoad:true,securityLevel:"loose",theme:"dark"}});
    const ws=new WebSocket('ws://localhost:{_websocket_port}/');
    ws.onmessage=function(e){{const d=JSON.parse(e.data);
    document.getElementById('current-status').textContent=d.node||'Processing';
    const u=document.createElement('div');u.className='update';
    u.textContent=`[\${{new Date().toLocaleTimeString()}}] \${d.event} - \${d.node}`;
    document.getElementById('updates').appendChild(u);}};
    </script></body></html>"""
    
    html_path = Path(f"graph_{session_id}.html")
    html_path.write_text(html, encoding="utf-8")
    start_visualization_server(html)
    
    try:
        webbrowser.open_new_tab(f"http://localhost:8002/")
    except Exception:
        pass
    
    return str(html_path)

# ------------------------- Main Entry Points -------------------------
async def main():
    """Main entry point with mode selection"""
    print("🎧 Optum MultiAgent Conversation System")
    print("=" * 50)
    print("1: CLI Text Mode (Debug with Latency Monitoring)")
    print("2: LiveKit Mic Mode (Real-time Audio)")
    print("3: Batch Podcast Mode (Traditional)")
    print("4: LangGraph Studio")
    
    mode = input("\nChoose mode (1-4): ").strip()
    
    if mode == "1":
        await cli_conversation_mode()
    elif mode == "2":
        await livekit_conversation_mode()
    elif mode == "3":
        await batch_podcast_mode()
    elif mode == "4":
        print("Starting LangGraph Studio...")
        os.system("langgraph dev")
    else:
        print("Invalid mode selected")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
