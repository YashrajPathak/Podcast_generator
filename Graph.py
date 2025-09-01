# graph.py - LangGraph Podcast Orchestrator
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
from dataclasses import dataclass

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command

# Import your existing components
from azure_openai_client import oai, llm, llm_safe
from azure_speech_client import synth, text_to_ssml, write_master
from conversation_dynamics import (
    _add_conversation_dynamics, 
    _add_emotional_reactions,
    _clean_repetition,
    ensure_complete_response
)

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
        },
        {
            "name": "compare_metrics",
            "description": "Compare two or more metrics",
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of metric names to compare"
                    },
                    "time_period": {"type": "string"},
                    "comparison_type": {"type": "string", "enum": ["correlation", "ratio", "difference"]}
                },
                "required": ["metrics", "time_period"]
            }
        },
        {
            "name": "validate_data_quality",
            "description": "Check data quality and integrity for metrics",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_name": {"type": "string"},
                    "checks": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["completeness", "consistency", "accuracy", "timeliness"]}
                    }
                },
                "required": ["metric_name"]
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
                
            elif call["name"] == "compare_metrics":
                result = await compare_metrics(
                    call["parameters"]["metrics"],
                    call["parameters"]["time_period"],
                    call["parameters"].get("comparison_type", "correlation")
                )
                results.append({
                    "tool": "compare_metrics",
                    "result": result,
                    "success": True
                })
                
            elif call["name"] == "validate_data_quality":
                result = await validate_data_quality(
                    call["parameters"]["metric_name"],
                    call["parameters"].get("checks", ["completeness", "consistency"])
                )
                results.append({
                    "tool": "validate_data_quality",
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

# Mock tool implementations (replace with real implementations)
async def analyze_metric_trend(metric_name: str, time_period: str, analysis_type: str) -> Dict:
    """Analyze metric trend - replace with actual implementation"""
    return {
        "metric": metric_name,
        "period": time_period,
        "analysis_type": analysis_type,
        "trend": "upward",
        "confidence": 0.85,
        "notes": "Significant growth observed in the last quarter"
    }

async def compare_metrics(metrics: List[str], time_period: str, comparison_type: str) -> Dict:
    """Compare metrics - replace with actual implementation"""
    return {
        "metrics": metrics,
        "period": time_period,
        "comparison_type": comparison_type,
        "results": {metric: random.uniform(0.7, 0.95) for metric in metrics},
        "insights": "Strong correlation between metrics"
    }

async def validate_data_quality(metric_name: str, checks: List[str]) -> Dict:
    """Validate data quality - replace with actual implementation"""
    return {
        "metric": metric_name,
        "checks_performed": checks,
        "results": {check: random.choice(["pass", "fail"]) for check in checks},
        "quality_score": random.uniform(0.8, 1.0)
    }

# ============ Agent Nodes ============
async def nexus_intro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Nexus introduction"""
    intro_text = (
        "Hello and welcome to Optum MultiAgent Conversation, where intelligence meets collaboration. "
        "I'm Agent Nexus, your host and guide through today's episode. "
        "In this podcast, we bring together specialized agents to explore the world of metrics, data, and decision-making. "
        "Let's meet today's experts."
    )
    
    audio_segment = await generate_audio(intro_text, "NEXUS")
    
    return {
        "messages": add_messages(state["messages"], [{"role": "assistant", "content": intro_text, "speaker": "Nexus"}]),
        "audio_segments": state["audio_segments"] + [audio_segment],
        "current_speaker": "Reco"
    }

async def reco_intro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Reco introduction"""
    intro_text = (
        "Hi everyone, I'm Agent Reco, your go-to for metric recommendations. "
        "I specialize in identifying the most impactful metrics for performance tracking, optimization, and strategic alignment."
    )
    
    audio_segment = await generate_audio(intro_text, "RECO")
    
    return {
        "messages": add_messages(state["messages"], [{"role": "assistant", "content": intro_text, "speaker": "Reco"}]),
        "audio_segments": state["audio_segments"] + [audio_segment],
        "current_speaker": "Stat"
    }

async def stat_intro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Stat introduction"""
    intro_text = (
        "Hello! I'm Agent Stat, focused on metric data. "
        "I dive deep into data sources, trends, and statistical integrity to ensure our metrics are not just smart—but solid."
    )
    
    audio_segment = await generate_audio(intro_text, "STAT")
    
    return {
        "messages": add_messages(state["messages"], [{"role": "assistant", "content": intro_text, "speaker": "Stat"}]),
        "audio_segments": state["audio_segments"] + [audio_segment],
        "current_speaker": "Nexus"
    }

async def nexus_topic_intro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Nexus introduces the topic"""
    topic_intro = await generate_topic_introduction(state["topic"])
    
    audio_segment = await generate_audio(topic_intro, "NEXUS")
    
    return {
        "messages": add_messages(state["messages"], [{"role": "assistant", "content": topic_intro, "speaker": "Nexus"}]),
        "audio_segments": state["audio_segments"] + [audio_segment],
        "current_speaker": "Reco",
        "current_turn": 1
    }

async def reco_turn_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Reco's turn with tool calling"""
    tools = get_metric_tools()
    
    # Get conversation context
    context = await build_conversation_context(state)
    
    # Generate response with tool calling
    response = await llm_with_tools(
        system=SYSTEM_RECO,
        context=context,
        tools=tools,
        conversation_history=state["conversation_history"]
    )
    
    # Process tool calls if any
    tool_results = []
    if hasattr(response, 'tool_calls') and response.tool_calls:
        tool_results = await execute_tools(response.tool_calls)
        # Generate follow-up response with tool results
        response = await llm_with_tools(
            system=SYSTEM_RECO,
            context={**context, "tool_results": tool_results},
            tools=tools,
            conversation_history=state["conversation_history"]
        )
    
    response_text = response.content if hasattr(response, 'content') else str(response)
    response_text = ensure_complete_response(response_text)
    
    # Add conversation dynamics
    response_text = _add_conversation_dynamics(
        response_text, "RECO", state["current_speaker"], 
        state["context"], state["current_turn"], state["conversation_history"]
    )
    response_text = _add_emotional_reactions(response_text, "RECO")
    response_text = _clean_repetition(response_text)
    
    audio_segment = await generate_audio(response_text, "RECO")
    
    return {
        "messages": add_messages(state["messages"], [{"role": "assistant", "content": response_text, "speaker": "Reco"}]),
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [
            {"role": "assistant", "content": response_text, "speaker": "Reco"}
        ],
        "current_speaker": "Stat",
        "current_turn": state["current_turn"] + 1,
        "interrupted": False
    }

async def stat_turn_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Stat's turn with tool calling"""
    tools = get_metric_tools()
    
    # Get conversation context
    context = await build_conversation_context(state)
    
    # Generate response with tool calling
    response = await llm_with_tools(
        system=SYSTEM_STAT,
        context=context,
        tools=tools,
        conversation_history=state["conversation_history"]
    )
    
    # Process tool calls if any
    tool_results = []
    if hasattr(response, 'tool_calls') and response.tool_calls:
        tool_results = await execute_tools(response.tool_calls)
        # Generate follow-up response with tool results
        response = await llm_with_tools(
            system=SYSTEM_STAT,
            context={**context, "tool_results": tool_results},
            tools=tools,
            conversation_history=state["conversation_history"]
        )
    
    response_text = response.content if hasattr(response, 'content') else str(response)
    response_text = ensure_complete_response(response_text)
    
    # Add conversation dynamics
    response_text = _add_conversation_dynamics(
        response_text, "STAT", state["current_speaker"], 
        state["context"], state["current_turn"], state["conversation_history"]
    )
    response_text = _add_emotional_reactions(response_text, "STAT")
    response_text = _clean_repetition(response_text)
    
    audio_segment = await generate_audio(response_text, "STAT")
    
    return {
        "messages": add_messages(state["messages"], [{"role": "assistant", "content": response_text, "speaker": "Stat"}]),
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [
            {"role": "assistant", "content": response_text, "speaker": "Stat"}
        ],
        "current_speaker": "Reco",
        "current_turn": state["current_turn"] + 1,
        "interrupted": False
    }

async def nexus_outro_node(state: PodcastState) -> Dict[str, Any]:
    """Agent Nexus conclusion"""
    outro_text = (
        "And that brings us to the end of today's episode of Optum MultiAgent Conversation. "
        "A big thank you to Agent Reco for guiding us through the art of metric recommendations, "
        "and to Agent Stat for grounding us in the power of metric data. "
        "Your insights today have not only informed but inspired. Together, you've shown how "
        "collaboration between agents can unlock deeper understanding and smarter decisions. "
        "To our listeners—thank you for tuning in. Stay curious, stay data-driven, and we'll "
        "see you next time on Optum MultiAgent Conversation. Until then, this is Agent Nexus, signing off."
    )
    
    audio_segment = await generate_audio(outro_text, "NEXUS")
    
    return {
        "messages": add_messages(state["messages"], [{"role": "assistant", "content": outro_text, "speaker": "Nexus"}]),
        "audio_segments": state["audio_segments"] + [audio_segment],
        "current_speaker": "END"
    }

# ============ Conditional Nodes ============
def should_continue(state: PodcastState) -> Literal["continue", "end", "interrupt"]:
    """Determine whether to continue, end, or interrupt"""
    if state["current_turn"] >= state["max_turns"]:
        return "end"
    
    if state["interrupted"]:
        return "interrupt"
    
    # 25% chance of interruption based on conversation content
    if state["conversation_history"]:
        last_message = state["conversation_history"][-1]["content"]
        should_interrupt = (
            "surprising" in last_message.lower() or 
            "controversial" in last_message.lower() or
            "shocking" in last_message.lower() or
            random.random() < 0.25
        )
        if should_interrupt:
            return "interrupt"
    
    return "continue"

def check_interruption(state: PodcastState) -> Literal["continue", "interrupt"]:
    """Check if interruption should occur"""
    return "interrupt" if state["interrupted"] else "continue"

async def handle_interruption_node(state: PodcastState) -> Dict[str, Any]:
    """Handle interruption gracefully"""
    last_message = state["conversation_history"][-1]["content"] if state["conversation_history"] else ""
    
    interruption_response = await llm(
        system=INTERRUPTION_SYSTEM,
        prompt=f"Interrupt the previous point gracefully: {last_message}"
    )
    
    interruption_text = ensure_complete_response(interruption_response)
    audio_segment = await generate_audio(interruption_text, "STAT")
    
    return {
        "messages": add_messages(state["messages"], [
            {"role": "assistant", "content": interruption_text, "speaker": "Stat", "interruption": True}
        ]),
        "audio_segments": state["audio_segments"] + [audio_segment],
        "conversation_history": state["conversation_history"] + [
            {"role": "assistant", "content": interruption_text, "speaker": "Stat", "interruption": True}
        ],
        "interrupted": True,
        "current_speaker": "Reco"  # Give floor back to Reco
    }

# ============ Helper Functions ============
async def generate_audio(text: str, role: str) -> str:
    """Generate audio for text and return file path"""
    ssml = text_to_ssml(text, role)
    audio_data = synth(ssml)
    
    # Save to temporary file
    temp_file = f"/tmp/{role}_{uuid.uuid4().hex}.wav"
    with wave.open(temp_file, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(audio_data)
    
    return temp_file

async def generate_topic_introduction(topic: str) -> str:
    """Generate topic introduction"""
    prompt = f"""
    Based on the topic: {topic}
    Provide a brief introduction (2-3 sentences) that sets the stage for discussion between metrics experts.
    Highlight the key aspects that would be interesting for a conversation between a metrics recommendation 
    specialist and a data integrity expert.
    """
    
    response = await llm(
        system=SYSTEM_NEXUS_TOPIC,
        prompt=prompt,
        max_tokens=120,
        temperature=0.4
    )
    
    return ensure_complete_response(response)

async def build_conversation_context(state: PodcastState) -> Dict[str, Any]:
    """Build context for the conversation"""
    recent_messages = state["conversation_history"][-3:] if len(state["conversation_history"]) > 3 else state["conversation_history"]
    
    return {
        "topic": state["topic"],
        "recent_conversation": recent_messages,
        "current_turn": state["current_turn"],
        "max_turns": state["max_turns"],
        "session_id": state["session_id"]
    }

async def llm_with_tools(system: str, context: Dict, tools: List[Dict], conversation_history: List[Dict]):
    """LLM call with tool support"""
    # Convert conversation history to prompt format
    history_text = "\n".join([
        f"{msg['speaker']}: {msg['content']}" for msg in conversation_history
    ])
    
    prompt = f"""
    Context: {json.dumps(context, indent=2)}
    
    Conversation History:
    {history_text}
    
    Continue the discussion based on the context and history above.
    """
    
    # This is a simplified version - in real implementation, you'd use LangGraph's tool calling
    response = await llm(
        system=system,
        prompt=prompt,
        max_tokens=150,
        temperature=0.45
    )
    
    return response

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
    builder.add_node("handle_interruption", handle_interruption_node)
    
    # Set entry point
    builder.set_entry_point("nexus_intro")
    
    # Define workflow
    builder.add_edge("nexus_intro", "reco_intro")
    builder.add_edge("reco_intro", "stat_intro")
    builder.add_edge("stat_intro", "nexus_topic_intro")
    builder.add_edge("nexus_topic_intro", "reco_turn")
    
    # Dynamic conversation flow
    builder.add_conditional_edges(
        "reco_turn",
        should_continue,
        {
            "continue": "stat_turn",
            "end": "nexus_outro",
            "interrupt": "handle_interruption"
        }
    )
    
    builder.add_conditional_edges(
        "stat_turn",
        should_continue,
        {
            "continue": "reco_turn",
            "end": "nexus_outro",
            "interrupt": "handle_interruption"
        }
    )
    
    builder.add_conditional_edges(
        "handle_interruption",
        check_interruption,
        {
            "continue": "stat_turn",
            "interrupt": "reco_turn"
        }
    )
    
    builder.add_edge("nexus_outro", END)
    
    return builder.compile()

# ============ Main Function ============
async def generate_podcast(topic: str, max_turns: int = 6, session_id: str = None) -> Dict[str, Any]:
    """Generate a complete podcast"""
    session_id = session_id or f"podcast_{uuid.uuid4().hex[:8]}"
    
    # Initialize state
    initial_state = PodcastState(
        messages=[],
        current_speaker="Nexus",
        topic=topic,
        context={},
        interrupted=False,
        audio_segments=[],
        conversation_history=[],
        current_turn=0,
        max_turns=max_turns,
        session_id=session_id
    )
    
    # Create and run graph
    graph = create_podcast_graph()
    final_state = await graph.ainvoke(initial_state)
    
    # Compile final audio
    final_audio_path = await compile_final_audio(final_state["audio_segments"], session_id)
    
    return {
        "session_id": session_id,
        "audio_path": final_audio_path,
        "conversation_history": final_state["conversation_history"],
        "total_turns": final_state["current_turn"],
        "audio_segments": final_state["audio_segments"]
    }

async def compile_final_audio(audio_segments: List[str], session_id: str) -> str:
    """Compile all audio segments into final WAV file"""
    if not audio_segments:
        return None
    
    output_path = f"/tmp/podcast_{session_id}.wav"
    
    # Use your existing write_master function
    write_master(audio_segments, output_path)
    
    return output_path

# ============ System Prompts ============
SYSTEM_RECO = """[Your existing Reco system prompt]"""
SYSTEM_STAT = """[Your existing Stat system prompt]"""
SYSTEM_NEXUS_TOPIC = """[Your existing Nexus topic introduction prompt]"""
INTERRUPTION_SYSTEM = """You are an expert at gracefully interrupting conversations. 
Provide a polite but firm interruption that acknowledges the previous point while introducing a new perspective or important consideration."""

# ============ FastAPI Endpoint ============
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class PodcastRequest(BaseModel):
    topic: str
    max_turns: int = 6
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
