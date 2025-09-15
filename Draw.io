<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net">
  <diagram name="Podcast Flow" id="podcast-flow-arch">
    <mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" page="1" pageWidth="1169" pageHeight="827">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>

        <!-- Entrypoint -->
        <mxCell id="A1" value="User / CLI / API" style="shape=ellipse;fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1">
          <mxGeometry x="500" y="20" width="160" height="60" as="geometry"/>
        </mxCell>

        <!-- API / Router -->
        <mxCell id="A2" value="API / Router&#10;- graph.py (CLI)&#10;- lkk.py (FastAPI)" style="rounded=1;fillColor=#e6f2ff;strokeColor=#1f78b4;" vertex="1" parent="1">
          <mxGeometry x="460" y="120" width="240" height="80" as="geometry"/>
        </mxCell>

        <!-- Config Manager -->
        <mxCell id="B1" value="Configuration Manager&#10;- Env Vars&#10;- Voice & LLM Params" style="rounded=1;fillColor=#ede7f6;strokeColor=#5e35b1;" vertex="1" parent="1">
          <mxGeometry x="120" y="240" width="220" height="80" as="geometry"/>
        </mxCell>

        <!-- Context Manager -->
        <mxCell id="B2" value="Context Manager&#10;- list_json_files()&#10;- load_context()&#10;- infer_topic()" style="rounded=1;fillColor=#fff2cc;strokeColor=#d6b656;" vertex="1" parent="1">
          <mxGeometry x="120" y="340" width="220" height="80" as="geometry"/>
        </mxCell>

        <!-- Orchestrator -->
        <mxCell id="C1" value="LangGraph Orchestrator&#10;- _build_compiled_graph()&#10;- generate_podcast()&#10;- _run_with_events()" style="rounded=1;fillColor=#e6ffe6;strokeColor=#33a02c;" vertex="1" parent="1">
          <mxGeometry x="460" y="260" width="280" height="100" as="geometry"/>
        </mxCell>

        <!-- Conversation Nodes -->
        <mxCell id="D1" value="Conversation Nodes&#10;- NEXUS_INTRO&#10;- RECO_INTRO&#10;- STAT_INTRO&#10;- NEXUS_TOPIC_INTRO&#10;- RECO_TURN / STAT_TURN (loop)&#10;- NEXUS_OUTRO" style="rounded=1;fillColor=#fff2e6;strokeColor=#ff7f0e;" vertex="1" parent="1">
          <mxGeometry x="460" y="400" width="280" height="160" as="geometry"/>
        </mxCell>

        <!-- LLM Service -->
        <mxCell id="E1" value="LLM Service&#10;- Azure OpenAI&#10;- llm_safe(), llm()" style="rounded=1;fillColor=#fde2e2;strokeColor=#c62828;" vertex="1" parent="1">
          <mxGeometry x="820" y="260" width="220" height="80" as="geometry"/>
        </mxCell>

        <!-- TTS Service -->
        <mxCell id="E2" value="TTS Service&#10;- text_to_ssml()&#10;- synth()" style="rounded=1;fillColor=#e0f7fa;strokeColor=#00838f;" vertex="1" parent="1">
          <mxGeometry x="820" y="360" width="220" height="80" as="geometry"/>
        </mxCell>

        <!-- Audio Processor -->
        <mxCell id="E3" value="Audio Processor&#10;- write_master()&#10;- wav_len()" style="rounded=1;fillColor=#f1f8e9;strokeColor=#558b2f;" vertex="1" parent="1">
          <mxGeometry x="820" y="460" width="220" height="80" as="geometry"/>
        </mxCell>

        <!-- Outputs -->
        <mxCell id="F1" value="Outputs&#10;- podcast_x.wav&#10;- script.txt&#10;- graph_convo.json&#10;- mermaid.html" style="shape=cylinder3;fillColor=#fffbe6;strokeColor=#827717;" vertex="1" parent="1">
          <mxGeometry x="480" y="600" width="240" height="100" as="geometry"/>
        </mxCell>

        <!-- External Services -->
        <mxCell id="G1" value="External Services&#10;- Azure OpenAI&#10;- Azure Speech" style="rounded=1;dashed=1;fillColor=#f5f5f5;strokeColor=#616161;" vertex="1" parent="1">
          <mxGeometry x="1080" y="320" width="200" height="100" as="geometry"/>
        </mxCell>

        <!-- Arrows -->
        <mxCell id="edge1" edge="1" parent="1" source="A1" target="A2" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge2" edge="1" parent="1" source="A2" target="B1" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge3" edge="1" parent="1" source="A2" target="C1" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge4" edge="1" parent="1" source="B1" target="C1" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge5" edge="1" parent="1" source="B2" target="C1" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge6" edge="1" parent="1" source="C1" target="D1" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge7" edge="1" parent="1" source="D1" target="E1" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge8" edge="1" parent="1" source="E1" target="E2" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge9" edge="1" parent="1" source="E2" target="E3" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge10" edge="1" parent="1" source="E3" target="F1" style="endArrow=block;strokeColor=#000000;"/>
        <mxCell id="edge11" edge="1" parent="1" source="E1" target="G1" style="dashed=1;strokeColor=#757575;endArrow=block;"/>
        <mxCell id="edge12" edge="1" parent="1" source="E2" target="G1" style="dashed=1;strokeColor=#757575;endArrow=block;"/>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
