<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net">
  <diagram name="Podcast System - Professional Flow" id="podflow-001">
    <mxGraphModel dx="1800" dy="1400" grid="1" gridSize="10" page="1" pageWidth="1800" pageHeight="1400">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>

        <!-- Top: Entrypoint -->
        <mxCell id="entry" value="Entrypoint: User / Scheduler / CLI / API" style="shape=ellipse;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=12;" vertex="1" parent="1">
          <mxGeometry x="740" y="40" width="320" height="68" as="geometry"/>
        </mxCell>

        <!-- API / Router -->
        <mxCell id="api" value="API / Router&#10;(graph.py CLI or lkk.py FastAPI)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=12;" vertex="1" parent="1">
          <mxGeometry x="720" y="140" width="360" height="84" as="geometry"/>
        </mxCell>

        <!-- Step label -->
        <mxCell id="step1" value="1" style="ellipse;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="1100" y="146" width="28" height="28" as="geometry"/>
        </mxCell>

        <!-- Left column: Config & Context -->
        <mxCell id="config" value="Configuration Manager&#10;(env, voice, LLM params)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="80" y="260" width="320" height="100" as="geometry"/>
        </mxCell>

        <mxCell id="context" value="Context Manager&#10;(list_json_files, load_context, infer_topic)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="80" y="380" width="320" height="110" as="geometry"/>
        </mxCell>

        <mxCell id="step2" value="2" style="ellipse;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="420" y="344" width="28" height="28" as="geometry"/>
        </mxCell>

        <!-- Center: Orchestrator -->
        <mxCell id="orchestrator" value="LangGraph Orchestrator (graph.py)&#10;_build_compiled_graph() / generate_podcast() / _run_with_events()" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=12;" vertex="1" parent="1">
          <mxGeometry x="520" y="260" width="480" height="120" as="geometry"/>
        </mxCell>

        <mxCell id="step3" value="3" style="ellipse;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="1020" y="288" width="28" height="28" as="geometry"/>
        </mxCell>

        <!-- Arrow: api -> orchestrator -->
        <mxCell id="a_api_orch" edge="1" parent="1" source="api" target="orchestrator" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- Arrows: config/context -> orchestrator -->
        <mxCell id="a_config_orch" edge="1" parent="1" source="config" target="orchestrator" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="a_context_orch" edge="1" parent="1" source="context" target="orchestrator" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- Right column: Engine pipeline -->
        <mxCell id="llm" value="LLM Service (lkk.py)&#10;llm_safe() / llm()" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="1120" y="240" width="360" height="100" as="geometry"/>
        </mxCell>

        <mxCell id="ssml" value="SSML / Text→SSML&#10;text_to_ssml(), _inflect()" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="1120" y="360" width="360" height="84" as="geometry"/>
        </mxCell>

        <mxCell id="tts" value="TTS Synth (Azure Speech)&#10;synth() -> wav segment" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="1120" y="460" width="360" height="84" as="geometry"/>
        </mxCell>

        <mxCell id="audio_proc" value="Audio Processor&#10;write_master(), wav_len()" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="1120" y="560" width="360" height="84" as="geometry"/>
        </mxCell>

        <!-- External services box (dashed) -->
        <mxCell id="external" value="External Services&#10;Azure OpenAI (LLM)&#10;Azure Speech (TTS)" style="rounded=1;whiteSpace=wrap;html=1;dashed=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="1480" y="120" width="240" height="120" as="geometry"/>
        </mxCell>

        <!-- Arrow: orchestrator -> node sequence (down) -->
        <mxCell id="a_orch_nodes" edge="1" parent="1" source="orchestrator" target="entry" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#bbbbbb;dashed=1;visible=0;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- Node sequence (stacked center) -->
        <mxCell id="nexus_intro" value="NEXUS_INTRO (nexus_intro_node)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="520" y="420" width="360" height="64" as="geometry"/>
        </mxCell>

        <mxCell id="reco_intro" value="RECO_INTRO (reco_intro_node)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="520" y="500" width="360" height="64" as="geometry"/>
        </mxCell>

        <mxCell id="stat_intro" value="STAT_INTRO (stat_intro_node)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="520" y="580" width="360" height="64" as="geometry"/>
        </mxCell>

        <mxCell id="topic_intro" value="NEXUS_TOPIC_INTRO (nexus_topic_intro_node)&#10;calls generate_nexus_topic_intro()" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="520" y="660" width="360" height="88" as="geometry"/>
        </mxCell>

        <!-- small step labels beside node flow -->
        <mxCell id="s31" value="3.1" style="ellipse;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="900" y="440" width="26" height="26" as="geometry"/>
        </mxCell>
        <mxCell id="s32" value="3.2" style="ellipse;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="900" y="520" width="26" height="26" as="geometry"/>
        </mxCell>
        <mxCell id="s33" value="3.3" style="ellipse;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="900" y="600" width="26" height="26" as="geometry"/>
        </mxCell>
        <mxCell id="s34" value="3.4" style="ellipse;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="900" y="684" width="26" height="26" as="geometry"/>
        </mxCell>

        <!-- Edges: orchestrator -> node flow -->
        <mxCell id="e_orch_n1" edge="1" parent="1" source="orchestrator" target="nexus_intro" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_n1_n2" edge="1" parent="1" source="nexus_intro" target="reco_intro" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_n2_n3" edge="1" parent="1" source="reco_intro" target="stat_intro" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_n3_topic" edge="1" parent="1" source="stat_intro" target="topic_intro" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- Loop area: Reco <-> Stat turns -->
        <mxCell id="reco_turn" value="RECO_TURN (reco_turn_node)&#10-> llm(SYSTEM_RECO)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="360" y="780" width="320" height="84" as="geometry"/>
        </mxCell>

        <mxCell id="stat_turn" value="STAT_TURN (stat_turn_node)&#10-> llm(SYSTEM_STAT)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="840" y="780" width="320" height="84" as="geometry"/>
        </mxCell>

        <!-- Decision diamond -->
        <mxCell id="decide" value="should_continue?&#10;(current_turn >= max_turns)" style="shape=rhombus;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="660" y="880" width="140" height="100" as="geometry"/>
        </mxCell>

        <!-- After loop: nexus outro -->
        <mxCell id="outro" value="NEXUS_OUTRO (nexus_outro_node)" style="rounded=1;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="660" y="1020" width="360" height="84" as="geometry"/>
        </mxCell>

        <!-- Edges in loop -->
        <mxCell id="e_topic_reco" edge="1" parent="1" source="topic_intro" target="reco_turn" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_reco_stat" edge="1" parent="1" source="reco_turn" target="stat_turn" style="edgeStyle=elbowEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_stat_decide" edge="1" parent="1" source="stat_turn" target="decide" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <!-- decide -> reco (loop continue) -->
        <mxCell id="e_decide_reco" edge="1" parent="1" source="decide" target="reco_turn" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <!-- decide -> outro (end) -->
        <mxCell id="e_decide_outro" edge="1" parent="1" source="decide" target="outro" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- Reco/Stat -> LLM pipeline -->
        <mxCell id="e_reco_llm" edge="1" parent="1" source="reco_turn" target="llm" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_stat_llm" edge="1" parent="1" source="stat_turn" target="llm" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- LLM -> SSML -> TTS -> Audio -> Outputs -->
        <mxCell id="e_llm_ssml" edge="1" parent="1" source="llm" target="ssml" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_ssml_tts" edge="1" parent="1" source="ssml" target="tts" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_tts_audio" edge="1" parent="1" source="tts" target="audio_proc" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_audio_out" edge="1" parent="1" source="audio_proc" target="outro" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;dashed=1;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- Outputs cylinder -->
        <mxCell id="outputs" value="Outputs & Storage&#10;podcast_YYYYMMDD.wav&#10;podcast_script.txt&#10;graph_convo.json&#10;mermaid.html" style="shape=cylinder3;whiteSpace=wrap;html=1;strokeColor=#222222;fillColor=#ffffff;fontSize=11;" vertex="1" parent="1">
          <mxGeometry x="620" y="1130" width="360" height="120" as="geometry"/>
        </mxCell>

        <!-- outro -> outputs -->
        <mxCell id="e_out_outs" edge="1" parent="1" source="outro" target="outputs" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#222222;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- External dashed dependencies from services -->
        <mxCell id="e_llm_ext" edge="1" parent="1" source="llm" target="external" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#999999;dashed=1;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="e_tts_ext" edge="1" parent="1" source="tts" target="external" style="edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;strokeColor=#999999;dashed=1;">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>

        <!-- Monitoring / WebSocket note (small) -->
        <mxCell id="monitor" value="Monitoring: WebSocket timeline (jsonl), Mermaid HTML visual" style="rounded=1;whiteSpace=wrap;html=1;fontSize=11;strokeColor=#222222;fillColor=#ffffff;" vertex="1" parent="1">
          <mxGeometry x="80" y="1130" width="420" height="120" as="geometry"/>
        </mxCell>

        <!-- Legend / small notes -->
        <mxCell id="legend" value="Legend: numbered steps show high-level sequence; dashed lines = external dependency or artifact" style="rounded=1;whiteSpace=wrap;html=1;fontSize=11;strokeColor=#222222;fillColor=#ffffff;" vertex="1" parent="1">
          <mxGeometry x="1120" y="1240" width="600" height="100" as="geometry"/>
        </mxCell>

      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
