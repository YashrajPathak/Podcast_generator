# graph.py - Updated LangGraph Podcast Orchestrator with UI visualization
import os
import re
import wave
import json
import uuid
import asyncio
import random
import tempfile
from pathlib import Path
from typing import Dict, List, Any, Optional, TypedDict, Literal
from datetime import datetime

# Install: pip install langgraph
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

# For LangGraph UI visualization
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.patches import BoxStyle

# ============ CORE FUNCTIONS ============
def ensure_complete_response(text: str) -> str:
    """Ensure response is a complete sentence"""
    text = text.strip()
    if text and text[-1] not in {'.', '!', '?'}:
        text += '.'
    return text

def _add_conversation_dynamics(text: str, role: str, last_speaker: str, context: str, turn_count: int, conversation_history: list) -> str:
    """Add conversational elements"""
    other_agent = "Stat" if role == "RECO" else "Reco" if role == "STAT" else ""
    
    # Add name references occasionally
    if other_agent and random.random() < 0.3 and turn_count > 2:
        if random.random() < 0.5:
            text = f"{other_agent}, {text}"
        else:
            text = f"{text}, {other_agent}"
    
    # Add conversational phrases
    conversational_phrases = [
        "You know, ", "I think, ", "Actually, ", "Well, ", "So, ",
        "Anyway, ", "Now, ", "Looking at this, ", "From what I see, "
    ]
    
    if random.random() < 0.4 and len(text.split()) > 5:
        phrase = random.choice(conversational_phrases)
        text = phrase + text[0].lower() + text[1:]
    
    return text

def _add_emotional_reactions(text: str, role: str) -> str:
    """Add emotional reactions"""
    emotional_triggers = {
        "dramatic": ["That's quite a dramatic shift! ", "This is significant! "],
        "concerning": ["This is concerning. ", "That worries me slightly. "],
        "positive": ["That's encouraging! ", "This is positive news. "],
        "surprising": ["That's surprising! ", "I didn't expect that. "]
    }
    
    # Add emotional reaction based on content
    for trigger, reactions in emotional_triggers.items():
        if trigger in text.lower() and random.random() < 0.6:
            text = random.choice(reactions) + text
            break
    
    return text

def _clean_repetition(text: str) -> str:
    """Clean up repetitive phrases"""
    text = re.sub(r'\b(Reco|Stat),\s+\1,?\s+', r'\1, ', text)
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text)
    return text

# ============ Type Definitions ============
class PodcastState(TypedDict):
    messages: List[Dict[str, Any]]
    current_speaker: str
    topic: str
    context: Dict[str, Any]
    interrupted: bool
    audio_segments: List[str]
    conversation_history: List[Dict[str, str]]
    current_turn: int
    max_turns: int
    session_id: str
    # For UI visualization
    node_history: List[Dict[str, Any]]
    current_node: str

# ============ Tool Definitions ============
def get_metric_tools():
    return [
        {
            "name": "analyze_metric_trend",
            "description": "Analyze trends in specific metrics over time",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_name": {"type": "string"},
                    "time_period": {"type": "string"},
                    "analysis_type": {"type": "string", "enum": ["trend", "seasonality", "volatility"]}
                },
                "required": ["metric_name", "time_period"]
            }
        }
    ]

async def execute_tools(tool_calls: List[Dict]) -> List[Dict]:
    """Execute tool calls and return results"""
    results = []
    for call in tool_calls:
        try:
            if call["name"] == "analyze_metric_trend":
                result = await analyze_metric_trend(
                    call["parameters"]["metric_name"],
                    call["parameters"]["time_period"],
                    call["parameters"].get("analysis_type", "trend")
                )
                results.append({
                    "tool": "analyze_metric_trend",
                    "result": result,
                    "success": True
                })
        except Exception as e:
            results.append({
                "tool": call["name"],
                "result": f"Error: {str(e)}",
                "success": False
            })
    return results

async def analyze_metric_trend(metric_name: str, time_period: str, analysis_type: str) -> Dict:
    """Analyze metric trend with realistic data"""
    trends = {
        "ASA": {
            "trend": "drastic_drop",
            "confidence": 0.92,
            "notes": "91% drop from 7,406s to 697s in January 2025 - requires validation"
        },
        "Call Duration": {
            "trend": "steady_decrease",
            "confidence": 0.78,
            "notes": "Gradual decrease from 16min to 9min over past year"
        },
        "Processing Time": {
            "trend": "volatile",
            "confidence": 0.85,
            "notes": "High volatility between 6.0-10.3 days - seasonal patterns detected"
        }
    }
    
    return trends.get(metric_name, {
        "trend": "unknown",
        "confidence": 0.0,
        "notes": f"No data available for {metric_name}"
    })

# ============ SIMPLIFIED LLM CLIENT ============
async def llm(system: str, prompt: str, max_tokens: int = 150, temperature: float = 0.45) -> str:
    """LLM call with dynamic responses"""
    responses = {
        "ASA": [
            "Based on the 91% ASA drop, I recommend immediate data validation before any operational changes.",
            "The dramatic ASA decrease suggests either system improvements or data issues; we should investigate both.",
            "Given the ASA volatility, a control chart would help distinguish real improvements from anomalies."
        ],
        "Call Duration": [
            "The steady call duration decrease indicates either efficiency gains or simpler inquiries; we should track resolution rates.",
            "With call duration dropping consistently, we might need to adjust staffing models and training programs.",
            "The call duration trend suggests we're handling calls faster, but we should verify quality isn't compromised."
        ],
        "Processing Time": [
            "The processing time volatility indicates inconsistent workflows; we should standardize procedures.",
            "Given the processing time fluctuations, a seasonal analysis might reveal pattern we can plan for.",
            "The processing time data shows we need better workload balancing across teams and time periods."
        ]
    }
    
    # Simple keyword-based response selection
    for key in responses:
        if key.lower() in prompt.lower():
            return random.choice(responses[key])
    
    # Fallback response
    return "Based on the data trends, I recommend further analysis to validate these patterns before taking action."

async def llm_with_tools(system: str, context: Dict, tools: List[Dict], conversation_history: List[Dict]):
    """LLM call with tool support"""
    response = await llm(system, json.dumps(context), 150, 0.45)
    return type('Obj', (), {'content': response, 'tool_calls': []})

# ============ SIMPLIFIED AUDIO FUNCTIONS ============
async def generate_audio(text: str, role: str) -> str:
    """Generate audio for text - SIMULATED"""
    temp_file = f"/tmp/{role}_{uuid.uuid4().hex}.wav"
    with wave.open(temp_file, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b'')  # Empty audio for simulation
    return temp_file

# ============ Agent Nodes ============
async def nexus_intro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Nexus introduction"""
    intro_text = (
        "Hello and welcome to Optum MultiAgent Conversation, where intelligence meets collaboration. "
        "I'm Agent Nexus, your host and guide through today's episode. "
        "In this podcast, we bring together specialized agents to explore the world of metrics, data, and decision-making. "
        "Let's meet today's experts."
    )
    
    # Generate audio
    audio_segment = await generate_audio(intro_text, "NEXUS")
    
    # Update state
    new_messages = add_messages(state["messages"], ("assistant", intro_text))
    
    return {
        "messages": new_messages,
        "current_speaker": "NEXUS",
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [{"role": "NEXUS", "text": intro_text}],
        "node_history": state["node_history"] + [{"node": "nexus_intro", "timestamp": datetime.now().isoformat()}],
        "current_node": "reco_intro"
    }

async def reco_intro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Reco introduction"""
    intro_text = (
        "Hi everyone, I'm Agent Reco, your go-to for metric recommendations. "
        "I specialize in identifying the most impactful metrics for performance tracking, optimization, and strategic alignment."
    )
    
    # Generate audio
    audio_segment = await generate_audio(intro_text, "RECO")
    
    # Update state
    new_messages = add_messages(state["messages"], ("assistant", intro_text))
    
    return {
        "messages": new_messages,
        "current_speaker": "RECO",
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [{"role": "RECO", "text": intro_text}],
        "node_history": state["node_history"] + [{"node": "reco_intro", "timestamp": datetime.now().isoformat()}],
        "current_node": "stat_intro"
    }

async def stat_intro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Stat introduction"""
    intro_text = (
        "Hello! I'm Agent Stat, focused on metric data. "
        "I dive deep into data sources, trends, and statistical integrity to ensure our metrics are not just smart—but solid."
    )
    
    # Generate audio
    audio_segment = await generate_audio(intro_text, "STAT")
    
    # Update state
    new_messages = add_messages(state["messages"], ("assistant", intro_text))
    
    return {
        "messages": new_messages,
        "current_speaker": "STAT",
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [{"role": "STAT", "text": intro_text}],
        "node_history": state["node_history"] + [{"node": "stat_intro", "timestamp": datetime.now().isoformat()}],
        "current_node": "nexus_topic_intro"
    }

async def nexus_topic_intro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Nexus introduces the topic"""
    topic_intro = f"Today we'll discuss {state['topic']} trends and data validation techniques for operational excellence."
    
    # Generate audio
    audio_segment = await generate_audio(topic_intro, "NEXUS")
    
    # Update state
    new_messages = add_messages(state["messages"], ("assistant", topic_intro))
    
    return {
        "messages": new_messages,
        "current_speaker": "NEXUS",
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [{"role": "NEXUS", "text": topic_intro}],
        "node_history": state["node_history"] + [{"node": "nexus_topic_intro", "timestamp": datetime.now().isoformat()}],
        "current_node": "reco_turn"
    }

async def reco_turn_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Reco's turn with tool calling"""
    tools = get_metric_tools()
    
    # Get the last message for context
    last_message = state["conversation_history"][-1]["text"] if state["conversation_history"] else state["topic"]
    
    # Generate response
    system_prompt = "You are Agent Reco, a metrics recommendation specialist. Provide concise, actionable insights."
    response_obj = await llm_with_tools(system_prompt, {"topic": state["topic"], "last_message": last_message}, tools, state["conversation_history"])
    response_text = response_obj.content
    
    # Add conversation dynamics
    response_text = _add_conversation_dynamics(
        response_text, "RECO", 
        state["current_speaker"], 
        state["topic"], 
        state["current_turn"],
        state["conversation_history"]
    )
    
    # Add emotional reactions
    response_text = _add_emotional_reactions(response_text, "RECO")
    
    # Clean repetition
    response_text = _clean_repetition(response_text)
    
    # Ensure complete response
    response_text = ensure_complete_response(response_text)
    
    # Generate audio
    audio_segment = await generate_audio(response_text, "RECO")
    
    # Update state
    new_messages = add_messages(state["messages"], ("assistant", response_text))
    
    return {
        "messages": new_messages,
        "current_speaker": "RECO",
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [{"role": "RECO", "text": response_text}],
        "current_turn": state["current_turn"] + 1,
        "node_history": state["node_history"] + [{"node": "reco_turn", "timestamp": datetime.now().isoformat()}],
        "current_node": "stat_turn"
    }

async def stat_turn_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Stat's turn with tool calling"""
    tools = get_metric_tools()
    
    # Get the last message for context
    last_message = state["conversation_history"][-1]["text"] if state["conversation_history"] else state["topic"]
    
    # Generate response
    system_prompt = "You are Agent Stat, a data integrity expert. Provide analytical, evidence-based insights."
    response_obj = await llm_with_tools(system_prompt, {"topic": state["topic"], "last_message": last_message}, tools, state["conversation_history"])
    response_text = response_obj.content
    
    # Add conversation dynamics
    response_text = _add_conversation_dynamics(
        response_text, "STAT", 
        state["current_speaker"], 
        state["topic"], 
        state["current_turn"],
        state["conversation_history"]
    )
    
    # Add emotional reactions
    response_text = _add_emotional_reactions(response_text, "STAT")
    
    # Clean repetition
    response_text = _clean_repetition(response_text)
    
    # Ensure complete response
    response_text = ensure_complete_response(response_text)
    
    # Generate audio
    audio_segment = await generate_audio(response_text, "STAT")
    
    # Update state
    new_messages = add_messages(state["messages"], ("assistant", response_text))
    
    return {
        "messages": new_messages,
        "current_speaker": "STAT",
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [{"role": "STAT", "text": response_text}],
        "node_history": state["node_history"] + [{"node": "stat_turn", "timestamp": datetime.now().isoformat()}],
        "current_node": "reco_turn" if state["current_turn"] < state["max_turns"] else "nexus_outro"
    }

async def nexus_outro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Nexus conclusion"""
    outro_text = (
        "And that brings us to the end of today's episode of Optum MultiAgent Conversation. "
        "A big thank you to Agent Reco and Agent Stat for their insights. "
        "Thank you for tuning in. Stay curious, stay data-driven!"
    )
    
    # Generate audio
    audio_segment = await generate_audio(outro_text, "NEXUS")
    
    # Update state
    new_messages = add_messages(state["messages"], ("assistant", outro_text))
    
    return {
        "messages": new_messages,
        "current_speaker": "NEXUS",
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [{"role": "NEXUS", "text": outro_text}],
        "node_history": state["node_history"] + [{"node": "nexus_outro", "timestamp": datetime.now().isoformat()}],
        "current_node": "END"
    }

# ============ Conditional Nodes ============
def should_continue(state: PodcastState) -> Literal["continue", "end"]:
    """Determine whether to continue or end"""
    if state["current_turn"] >= state["max_turns"]:
        return "end"
    return "continue"

# ============ Graph Construction ============
def create_podcast_graph() -> StateGraph:
    """Create the LangGraph for podcast generation"""
    builder = StateGraph(PodcastState)
    
    # Add nodes
    builder.add_node("nexus_intro", nexus_intro_node)
    builder.add_node("reco_intro", reco_intro_node)
    builder.add_node("stat_intro", stat_intro_node)
    builder.add_node("nexus_topic_intro", nexus_topic_intro_node)
    builder.add_node("reco_turn", reco_turn_node)
    builder.add_node("stat_turn", stat_turn_node)
    builder.add_node("nexus_outro", nexus_outro_node)
    
    # Set entry point
    builder.set_entry_point("nexus_intro")
    
    # Add edges
    builder.add_edge("nexus_intro", "reco_intro")
    builder.add_edge("reco_intro", "stat_intro")
    builder.add_edge("stat_intro", "nexus_topic_intro")
    builder.add_edge("nexus_topic_intro", "reco_turn")
    builder.add_edge("reco_turn", "stat_turn")
    builder.add_conditional_edges(
        "stat_turn",
        should_continue,
        {
            "continue": "reco_turn",
            "end": "nexus_outro"
        }
    )
    builder.add_edge("nexus_outro", END)
    
    # Compile graph
    return builder.compile()

# ============ UI Visualization ============
def visualize_graph(graph, state: PodcastState, save_path: str = "graph_visualization.png"):
    """Create a visualization of the graph with current node highlighted"""
    plt.figure(figsize=(12, 8))
    
    # Create a directed graph
    G = nx.DiGraph()
    
    # Add nodes with labels
    nodes = [
        "nexus_intro", "reco_intro", "stat_intro", 
        "nexus_topic_intro", "reco_turn", "stat_turn", "nexus_outro", "END"
    ]
    
    for node in nodes:
        G.add_node(node)
    
    # Add edges
    edges = [
        ("nexus_intro", "reco_intro"),
        ("reco_intro", "stat_intro"),
        ("stat_intro", "nexus_topic_intro"),
        ("nexus_topic_intro", "reco_turn"),
        ("reco_turn", "stat_turn"),
        ("stat_turn", "reco_turn"),  # Conditional
        ("stat_turn", "nexus_outro"),  # Conditional
        ("nexus_outro", "END")
    ]
    
    for edge in edges:
        G.add_edge(edge[0], edge[1])
    
    # Position nodes
    pos = {
        "nexus_intro": (0, 3),
        "reco_intro": (1, 2),
        "stat_intro": (2, 2),
        "nexus_topic_intro": (1.5, 1),
        "reco_turn": (1, 0),
        "stat_turn": (2, 0),
        "nexus_outro": (1.5, -1),
        "END": (1.5, -2)
    }
    
    # Draw nodes
    node_colors = []
    for node in nodes:
        if node == state.get("current_node", ""):
            node_colors.append('red')  # Current node
        elif node in [h["node"] for h in state.get("node_history", [])]:
            node_colors.append('lightgreen')  # Visited nodes
        else:
            node_colors.append('lightblue')  # Not visited yet
    
    nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=2000)
    nx.draw_networkx_labels(G, pos, font_weight='bold')
    
    # Draw edges
    nx.draw_networkx_edges(G, pos, edgelist=edges, arrowstyle='->', arrowsize=20)
    
    # Add edge labels for conditional edges
    edge_labels = {
        ("stat_turn", "reco_turn"): "continue",
        ("stat_turn", "nexus_outro"): "end"
    }
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels)
    
    # Add title with current node info
    plt.title(f"Podcast Generation Graph\nCurrent Node: {state.get('current_node', 'N/A')}")
    
    # Save visualization
    plt.savefig(save_path)
    plt.close()
    
    return save_path

# ============ Main Function ============
async def generate_podcast(topic: str, max_turns: int = 4, session_id: str = None) -> Dict[str, Any]:
    """Generate a complete podcast"""
    session_id = session_id or f"podcast_{uuid.uuid4().hex[:8]}"
    
    # Initialize state
    initial_state: PodcastState = {
        "messages": [],
        "current_speaker": "NEXUS",
        "topic": topic,
        "context": {},
        "interrupted": False,
        "audio_segments": [],
        "conversation_history": [],
        "current_turn": 0,
        "max_turns": max_turns,
        "session_id": session_id,
        "node_history": [],
        "current_node": "nexus_intro"
    }
    
    # Create and run graph
    graph = create_podcast_graph()
    
    # Generate visualization before execution
    visualize_graph(graph, initial_state, f"graph_start_{session_id}.png")
    
    # Execute graph
    final_state = await graph.ainvoke(initial_state)
    
    # Generate visualization after execution
    visualize_graph(graph, final_state, f"graph_end_{session_id}.png")
    
    return {
        "session_id": session_id,
        "audio_segments": final_state["audio_segments"],
        "conversation_history": final_state["conversation_history"],
        "node_history": final_state["node_history"],
        "graph_images": {
            "start": f"graph_start_{session_id}.png",
            "end": f"graph_end_{session_id}.png"
        }
    }

# ============ FastAPI Endpoint ============
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI()

class PodcastRequest(BaseModel):
    topic: str
    max_turns: int = 4
    session_id: Optional[str] = None

@app.post("/generate-podcast")
async def api_generate_podcast(request: PodcastRequest):
    try:
        result = await generate_podcast(
            topic=request.topic,
            max_turns=request.max_turns,
            session_id=request.session_id
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/graph-visualization/{session_id}")
async def get_graph_visualization(session_id: str):
    """Get the graph visualization for a session"""
    start_path = f"graph_start_{session_id}.png"
    end_path = f"graph_end_{session_id}.png"
    
    if not os.path.exists(start_path) or not os.path.exists(end_path):
        raise HTTPException(status_code=404, detail="Visualization not found")
    
    return {
        "start_visualization": f"/visualizations/{start_path}",
        "end_visualization": f"/visualizations/{end_path}"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "langgraph-podcast", "port": 8002}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002, reload=True)
