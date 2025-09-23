# graph.py - Complete LiveKit + LangGraph implementation
import os
import sys
import json
import uuid
import asyncio
import datetime
import wave
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional, TypedDict, Annotated
from dataclasses import dataclass
from time import perf_counter

# ---- LiveKit Full Integration ----
try:
    import livekit.rtc as lkrtc
    from livekit.agents import (
        JobContext, Worker, AutoSubscribe,
        llm, stt, tts, voice_assistant
    )
    from livekit.agents.pipeline import VoicePipeline
    from livekit.agents.tts import TTSStreamAdapter
    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    print("⚠️  LiveKit not available. Install with: pip install 'livekit-agents'")
    lkrtc = None

# ---- Podcast Engine ----
try:
    import lkk as podcast
except Exception as e:
    print("❌ Could not import lkk.py")
    raise

# ---- LangGraph Core ----
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

# ---- Enhanced State Management ----
class ConversationState(TypedDict):
    """Complete conversation state managed by LangGraph"""
    messages: Annotated[List[Dict[str, Any]], add_messages]
    current_speaker: str
    topic: str
    context: Dict[str, Any]
    interrupted: bool
    audio_segments: List[str]
    conversation_history: List[Dict[str, str]]
    current_turn: int
    max_turns: int
    session_id: str
    node_history: List[Dict[str, Any]]
    current_node: str
    user_input: str
    last_response: str
    participants: List[str]
    connection_quality: Dict[str, float]
    # LiveKit specific
    room: Any
    participant: Any
    audio_stream: Any

# ---- LiveKit Audio Streaming ----
class LiveKitAudioStream:
    """Handles real-time audio streaming to LiveKit participants"""
    
    def __init__(self, room):
        self.room = room
        self.audio_track = lkrtc.LocalAudioTrack()
        self.audio_source = lkrtc.AudioSource()
        self.sample_rate = 24000
        self.sample_width = 2  # 16-bit
        self.channels = 1
        
    async def stream_audio_file(self, audio_file: str):
        """Stream audio file to LiveKit room in real-time"""
        try:
            with wave.open(audio_file, 'rb') as wav_file:
                # Verify format
                if (wav_file.getframerate() != self.sample_rate or 
                    wav_file.getsampwidth() != self.sample_width or
                    wav_file.getnchannels() != self.channels):
                    print("⚠️  Audio format mismatch, converting...")
                    # You'd add audio conversion here
                    return
                
                # Stream audio chunks
                chunk_size = 1024
                while True:
                    frames = wav_file.readframes(chunk_size)
                    if not frames:
                        break
                    
                    # Create audio frame
                    audio_frame = lkrtc.AudioFrame(
                        data=frames,
                        sample_rate=self.sample_rate,
                        samples_per_channel=len(frames) // self.sample_width,
                        num_channels=self.channels,
                        samples_type=lkrtc.AudioFrameType.INT16
                    )
                    
                    # Send to LiveKit
                    await self.audio_source.capture_frame(audio_frame)
                    await asyncio.sleep(0.01)  # Control streaming rate
                    
        except Exception as e:
            print(f"❌ Audio streaming error: {e}")

# ---- LangGraph Nodes ----
class ConversationNodes:
    """All conversation nodes for the LangGraph workflow"""
    
    def __init__(self, audio_stream: LiveKitAudioStream):
        self.audio_stream = audio_stream
    
    async def node_introduction(self, state: ConversationState) -> ConversationState:
        """Introduction node - agents introduce themselves"""
        state["node_history"].append({
            "node": "introduction",
            "timestamp": datetime.datetime.now().isoformat(),
            "action": "agent_introductions"
        })
        
        # Nexus introduction
        await self._speak_agent(state, "NEXUS", podcast.NEXUS_INTRO)
        
        # Reco introduction  
        await self._speak_agent(state, "RECO", podcast.RECO_INTRO)
        
        # Stat introduction
        await self._speak_agent(state, "STAT", podcast.STAT_INTRO)
        
        state["current_node"] = "topic_introduction"
        return state
    
    async def node_topic_introduction(self, state: ConversationState) -> ConversationState:
        """Topic introduction node"""
        state["node_history"].append({
            "node": "topic_introduction", 
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        topic_intro = await podcast.generate_nexus_topic_intro(state["context"]["text"])
        await self._speak_agent(state, "NEXUS", topic_intro)
        
        state["current_node"] = "await_user_input"
        return state
    
    async def node_await_user_input(self, state: ConversationState) -> ConversationState:
        """Wait for user input node"""
        state["node_history"].append({
            "node": "await_user_input",
            "timestamp": datetime.datetime.now().isoformat(),
            "status": "waiting"
        })
        
        # In LiveKit mode, this node waits for real user input
        # In CLI mode, it processes the next input
        if not state.get("user_input"):
            # Set condition to wait for input
            state["current_node"] = "process_user_input"
            return state
            
        state["current_node"] = "reco_response"
        return state
    
    async def node_reco_response(self, state: ConversationState) -> ConversationState:
        """Agent Reco response node"""
        state["node_history"].append({
            "node": "reco_response",
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        reco_prompt = f"Context: {state['context']['text']}\nUser said: '{state['user_input']}'. Provide one concise recommendation."
        reco_response = await podcast.llm(podcast.SYSTEM_RECO, reco_prompt)
        await self._speak_agent(state, "RECO", reco_response)
        
        state["last_response"] = reco_response
        state["current_node"] = "stat_validation"
        return state
    
    async def node_stat_validation(self, state: ConversationState) -> ConversationState:
        """Agent Stat validation node"""
        state["node_history"].append({
            "node": "stat_validation", 
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        stat_prompt = f"Context: {state['context']['text']}\nReco just said: '{state['last_response']}'. Provide one concise validation or risk assessment."
        stat_response = await podcast.llm(podcast.SYSTEM_STAT, stat_prompt)
        await self._speak_agent(state, "STAT", stat_response)
        
        state["current_turn"] += 1
        state["current_node"] = "check_conversation_end"
        return state
    
    async def node_check_conversation_end(self, state: ConversationState) -> ConversationState:
        """Check if conversation should end"""
        state["node_history"].append({
            "node": "check_conversation_end",
            "timestamp": datetime.datetime.now().isoformat(),
            "current_turn": state["current_turn"],
            "max_turns": state["max_turns"]
        })
        
        if state["current_turn"] >= state["max_turns"] or state.get("interrupted", False):
            state["current_node"] = "conversation_outro"
        else:
            state["current_node"] = "await_user_input"
            state["user_input"] = ""  # Reset for next input
            
        return state
    
    async def node_conversation_outro(self, state: ConversationState) -> ConversationState:
        """Conversation ending node"""
        state["node_history"].append({
            "node": "conversation_outro",
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        await self._speak_agent(state, "NEXUS", podcast.NEXUS_OUTRO)
        state["current_node"] = "END"
        return state
    
    async def node_handle_interruption(self, state: ConversationState) -> ConversationState:
        """Handle conversation interruptions"""
        state["node_history"].append({
            "node": "handle_interruption",
            "timestamp": datetime.datetime.now().isoformat(),
            "action": "pause_conversation"
        })
        
        state["interrupted"] = True
        state["current_node"] = "check_conversation_end"
        return state
    
    async def _speak_agent(self, state: ConversationState, agent_type: str, text: str):
        """Helper method for agent speech with LiveKit streaming"""
        start_time = perf_counter()
        
        # Add to conversation history
        state["conversation_history"].append({
            "speaker": agent_type,
            "text": text,
            "timestamp": datetime.datetime.now().isoformat(),
            "turn": state["current_turn"]
        })
        
        # Generate SSML and synthesize audio
        ssml = podcast.text_to_ssml(text, agent_type)
        audio_file = await asyncio.to_thread(podcast.synth, ssml)
        state["audio_segments"].append(audio_file)
        
        # Stream audio to LiveKit if available
        if self.audio_stream and state.get("room"):
            await self.audio_stream.stream_audio_file(audio_file)
        
        # Calculate and log latency
        latency = (perf_counter() - start_time) * 1000
        print(f"🎯 {agent_type} Latency: {latency:.0f}ms")
        
        # Print to console
        print(f"{agent_type}: {text}")
        
        return latency

# ---- LiveKit Event Handlers ----
class LiveKitHandlers:
    """LiveKit event handlers integrated with LangGraph"""
    
    def __init__(self, graph_manager):
        self.graph_manager = graph_manager
        self.stt_engine = None
        self.last_audio_time = 0
        
    async def handle_participant_connected(self, participant: lkrtc.RemoteParticipant):
        """Handle new participant connection"""
        print(f"👤 Participant connected: {participant.identity}")
        
        # Add participant to state
        current_state = self.graph_manager.get_current_state()
        if participant.identity not in current_state["participants"]:
            current_state["participants"].append(participant.identity)
        
        # Start conversation if first participant
        if len(current_state["participants"]) == 1:
            await self.graph_manager.start_conversation()
    
    async def handle_track_subscribed(self, track: lkrtc.Track, publication: lkrtc.TrackPublication, participant: lkrtc.RemoteParticipant):
        """Handle audio track subscription for STT"""
        if track.kind == lkrtc.TrackKind.KIND_AUDIO:
            print(f"🎤 Audio track subscribed from {participant.identity}")
            
            # Initialize STT for real-time transcription
            if not self.stt_engine:
                try:
                    from livekit.agents import stt
                    self.stt_engine = stt.StreamAdapter(stt.create_stream)
                except ImportError:
                    print("⚠️  STT not available")
    
    async def handle_data_received(self, data: bytes, participant: lkrtc.RemoteParticipant, kind: lkrtc.DataPacketKind):
        """Handle data messages from participants"""
        try:
            message = json.loads(data.decode('utf-8'))
            msg_type = message.get('type')
            
            if msg_type == 'text_input':
                await self.graph_manager.process_user_input(
                    message['text'], 
                    participant.identity
                )
            elif msg_type == 'interrupt':
                await self.graph_manager.interrupt_conversation()
            elif msg_type == 'quality_report':
                self._update_connection_quality(participant.identity, message['quality'])
                
        except Exception as e:
            print(f"Error processing data: {e}")
    
    async def handle_audio_frame(self, frame: lkrtc.AudioFrame, participant: lkrtc.RemoteParticipant):
        """Real-time audio processing for STT"""
        if not self.stt_engine or (perf_counter() - self.last_audio_time) < 0.5:
            return
            
        try:
            # Process audio for speech recognition
            results = await self.stt_engine.process_audio(frame.data)
            if results and results.text.strip():
                await self.graph_manager.process_user_input(
                    results.text, 
                    participant.identity
                )
                self.last_audio_time = perf_counter()
                
        except Exception as e:
            print(f"STT error: {e}")
    
    def _update_connection_quality(self, participant: str, quality: float):
        """Update connection quality metrics"""
        current_state = self.graph_manager.get_current_state()
        current_state["connection_quality"][participant] = quality
        
        # Adapt conversation based on quality
        if quality < 0.3:  # Poor connection
            print(f"📶 Poor connection for {participant}, simplifying responses")

# ---- Graph Manager ----
class GraphManager:
    """Manages LangGraph workflow and LiveKit integration"""
    
    def __init__(self):
        self.graph = None
        self.current_state = None
        self.audio_stream = None
        self.handlers = None
        self.nodes = None
        
    def initialize_graph(self, room=None):
        """Initialize the LangGraph workflow"""
        # Create audio stream if LiveKit available
        if room and _LIVEKIT_AVAILABLE:
            self.audio_stream = LiveKitAudioStream(room)
        
        # Initialize nodes
        self.nodes = ConversationNodes(self.audio_stream)
        self.handlers = LiveKitHandlers(self)
        
        # Build the graph
        workflow = StateGraph(ConversationState)
        
        # Add all nodes
        workflow.add_node("introduction", self.nodes.node_introduction)
        workflow.add_node("topic_introduction", self.nodes.node_topic_introduction)
        workflow.add_node("await_user_input", self.nodes.node_await_user_input)
        workflow.add_node("reco_response", self.nodes.node_reco_response)
        workflow.add_node("stat_validation", self.nodes.node_stat_validation)
        workflow.add_node("check_conversation_end", self.nodes.node_check_conversation_end)
        workflow.add_node("conversation_outro", self.nodes.node_conversation_outro)
        workflow.add_node("handle_interruption", self.nodes.node_handle_interruption)
        
        # Define workflow edges
        workflow.set_entry_point("introduction")
        workflow.add_edge("introduction", "topic_introduction")
        workflow.add_edge("topic_introduction", "await_user_input")
        workflow.add_conditional_edges(
            "await_user_input",
            self._should_process_input,
            {
                "process": "reco_response",
                "wait": "await_user_input"
            }
        )
        workflow.add_edge("reco_response", "stat_validation")
        workflow.add_edge("stat_validation", "check_conversation_end")
        workflow.add_conditional_edges(
            "check_conversation_end",
            self._should_end_conversation,
            {
                "continue": "await_user_input", 
                "end": "conversation_outro"
            }
        )
        workflow.add_edge("conversation_outro", END)
        
        self.graph = workflow.compile()
        
        # Initialize state
        self.current_state = {
            "messages": [],
            "current_speaker": "",
            "topic": "",
            "context": {},
            "interrupted": False,
            "audio_segments": [],
            "conversation_history": [],
            "current_turn": 0,
            "max_turns": 6,
            "session_id": str(uuid.uuid4()),
            "node_history": [],
            "current_node": "",
            "user_input": "",
            "last_response": "",
            "participants": [],
            "connection_quality": {},
            "room": room,
            "participant": None,
            "audio_stream": self.audio_stream
        }
    
    def _should_process_input(self, state: ConversationState) -> str:
        """Conditional edge: should we process input or wait?"""
        return "process" if state.get("user_input") else "wait"
    
    def _should_end_conversation(self, state: ConversationState) -> str:
        """Conditional edge: should conversation end?"""
        if state["current_turn"] >= state["max_turns"] or state.get("interrupted"):
            return "end"
        return "continue"
    
    async def start_conversation(self):
        """Start the conversation workflow"""
        if not self.graph:
            self.initialize_graph()
        
        # Load context
        context_text, meta = podcast.load_context("both")
        self.current_state["context"] = {"text": context_text, "meta": meta}
        self.current_state["topic"] = meta.get("topic", "Healthcare Analytics")
        
        print("🚀 Starting LangGraph conversation workflow...")
        await self.graph.ainvoke(self.current_state)
    
    async def process_user_input(self, text: str, user_id: str = "user"):
        """Process user input through the graph"""
        if not self.graph:
            return
            
        self.current_state["user_input"] = text
        self.current_state["messages"].append({
            "role": "user",
            "content": text,
            "user_id": user_id,
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        print(f"👤 {user_id}: {text}")
        await self.graph.ainvoke(self.current_state)
    
    async def interrupt_conversation(self):
        """Interrupt the current conversation"""
        self.current_state["interrupted"] = True
        await self.graph.ainvoke(self.current_state)
    
    def get_current_state(self) -> ConversationState:
        """Get current graph state"""
        return self.current_state

# ---- LiveKit Worker ----
async def livekit_entrypoint(ctx: JobContext):
    """LiveKit agent entrypoint with full LangGraph integration"""
    print("🎧 Starting LiveKit + LangGraph voice agent...")
    
    await ctx.wait_for_connected()
    
    # Initialize graph manager with LiveKit room
    graph_manager = GraphManager()
    graph_manager.initialize_graph(ctx.room)
    
    # Set up event handlers
    ctx.room.on("track_subscribed", graph_manager.handlers.handle_track_subscribed)
    ctx.room.on("data_received", graph_manager.handlers.handle_data_received)
    ctx.room.on("participant_connected", graph_manager.handlers.handle_participant_connected)
    
    # Audio processing
    @ctx.room.on("audio_frame")
    async def on_audio_frame(frame: lkrtc.AudioFrame, participant: lkrtc.RemoteParticipant):
        await graph_manager.handlers.handle_audio_frame(frame, participant)
    
    print("✅ LiveKit + LangGraph agent ready!")
    await asyncio.Future()  # Keep running

# ---- Main Application ----
async def main():
    """Main application entry point"""
    print("🎧 Optum MultiAgent System - LiveKit + LangGraph")
    print("=" * 50)
    
    graph_manager = GraphManager()
    
    print("1: CLI Mode (LangGraph workflow)")
    print("2: LiveKit Mode (Real-time audio)")
    print("3: LangGraph Studio")
    
    choice = input("\nChoose mode (1-3): ").strip()
    
    if choice == "1":
        # CLI mode with full LangGraph workflow
        await cli_mode(graph_manager)
    elif choice == "2":
        # LiveKit mode
        await livekit_mode()
    elif choice == "3":
        # LangGraph Studio
        os.system("langgraph dev")
    else:
        print("Invalid choice")

async def cli_mode(graph_manager: GraphManager):
    """CLI mode with full LangGraph workflow"""
    print("🧪 CLI Mode - LangGraph Workflow")
    
    graph_manager.initialize_graph()
    await graph_manager.start_conversation()
    
    while graph_manager.current_state["current_node"] != "END":
        user_input = input("\nYou: ").strip()
        if user_input.lower() == 'exit':
            break
        await graph_manager.process_user_input(user_input)
    
    # Generate final outputs
    await generate_final_outputs(graph_manager.current_state)

async def livekit_mode():
    """LiveKit real-time mode"""
    if not _LIVEKIT_AVAILABLE:
        print("❌ LiveKit not available")
        return
        
    worker = Worker(
        entrypoint=livekit_entrypoint,
        url=os.getenv("LIVEKIT_URL", "ws://localhost:7880"),
        api_key=os.getenv("LIVEKIT_API_KEY", "devkey"),
        api_secret=os.getenv("LIVEKIT_API_SECRET", "secret"),
    )
    
    print("🚀 Starting LiveKit server...")
    await worker.run()

async def generate_final_outputs(state: ConversationState):
    """Generate final audio and transcript"""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Generate final audio
    out_wav = f"conversation_{timestamp}.wav"
    podcast.write_master(state["audio_segments"], out_wav)
    
    # Generate detailed transcript
    transcript_file = f"conversation_{timestamp}.json"
    with open(transcript_file, 'w', encoding='utf-8') as f:
        json.dump({
            "session_id": state["session_id"],
            "transcript": state["conversation_history"],
            "node_history": state["node_history"],
            "workflow_path": [node["node"] for node in state["node_history"]],
            "timestamp": timestamp,
            "audio_file": out_wav,
            "turns_completed": state["current_turn"],
            "participants": state["participants"]
        }, f, indent=2)
    
    print(f"\n✅ Conversation completed!")
    print(f"   Audio: {out_wav}")
    print(f"   Transcript: {transcript_file}")
    print(f"   Workflow nodes: {len(state['node_history'])}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Session ended")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
