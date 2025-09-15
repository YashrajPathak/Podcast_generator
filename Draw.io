<mxfile host="app.diagrams.net">
  <diagram name="Podcast Generator Architecture" id="podcast-arch-pro">
    <mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" page="1" pageWidth="827" pageHeight="1169">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        
        <!-- ENTRY POINTS -->
        <mxCell id="A1" value="Entry Points" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="120" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="A2" value="graph.py (CLI)" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="110" width="120" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="A3" value="lkk.py (CLI)" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="150" width="120" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="A4" value="FastAPI Server" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="190" width="120" height="30" as="geometry"/>
        </mxCell>

        <!-- CONFIGURATION -->
        <mxCell id="B1" value="Configuration Manager" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="40" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="B2" value="Environment Variables" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="110" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="B3" value="Voice Parameters" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="150" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="B4" value="LLM Parameters" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="190" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- CONTEXT MANAGER -->
        <mxCell id="C1" value="Context Manager" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="380" y="40" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="C2" value="JSON File Selection" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="380" y="110" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="C3" value="Data Loading & Parsing" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="380" y="150" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="C4" value="Topic Inference" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="380" y="190" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- ORCHESTRATOR -->
        <mxCell id="D1" value="LangGraph Orchestrator" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="560" y="40" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="D2" value="Graph Construction" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="560" y="110" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="D3" value="State Management" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="560" y="150" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="D4" value="Event Streaming" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="560" y="190" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- CONVERSATION MANAGER -->
        <mxCell id="E1" value="Conversation Manager" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="740" y="40" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="E2" value="Node Definitions" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="740" y="110" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="E3" value="Turn Management" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="740" y="150" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="E4" value="Flow Control" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="740" y="190" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- LLM SERVICE -->
        <mxCell id="F1" value="LLM Service" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="280" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="F2" value="Azure OpenAI Integration" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="350" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="F3" value="Prompt Management" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="390" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="F4" value="Response Validation" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="430" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- TTS SERVICE -->
        <mxCell id="G1" value="TTS Service" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="220" y="280" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="G2" value="Azure Speech Integration" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="220" y="350" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="G3" value="SSML Generation" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="220" y="390" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="G4" value="Audio Synthesis" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="220" y="430" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- AUDIO PROCESSOR -->
        <mxCell id="H1" value="Audio Processor" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="400" y="280" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="H2" value="Segment Management" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="400" y="350" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="H3" value="Audio Concatenation" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="400" y="390" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="H4" value="Format Conversion" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="400" y="430" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- MONITORING -->
        <mxCell id="I1" value="Monitoring Service" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="580" y="280" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="I2" value="Performance Metrics" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="580" y="350" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="I3" value="Real-time Events" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="580" y="390" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="I4" value="WebSocket Server" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="580" y="430" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- OUTPUT MANAGER -->
        <mxCell id="J1" value="Output Manager" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="760" y="280" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="J2" value="File Generation" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="760" y="350" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="J3" value="Format Management" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="760" y="390" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="J4" value="Metadata Creation" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="760" y="430" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- EXTERNAL SERVICES -->
        <mxCell id="K1" value="External Services" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="940" y="40" width="140" height="60" as="geometry"/>
        </mxCell>
        <mxCell id="K2" value="Azure OpenAI" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="940" y="110" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="K3" value="Azure Speech Service" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="940" y="150" width="140" height="30" as="geometry"/>
        </mxCell>
        <mxCell id="K4" value="Storage (Future)" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="940" y="190" width="140" height="30" as="geometry"/>
        </mxCell>

        <!-- DATA FLOW ARROWS -->
        <mxCell id="edge1" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="A1" target="B1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge2" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="B1" target="C1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge3" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="C1" target="D1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge4" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="D1" target="E1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge5" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="E1" target="F1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge6" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="E1" target="G1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge7" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="F1" target="H1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge8" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="G1" target="H1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge9" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="H1" target="J1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge10" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" edge="1" parent="1" source="D1" target="I1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge11" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;dashed=1;" edge="1" parent="1" source="F1" target="K2">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge12" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;dashed=1;" edge="1" parent="1" source="G1" target="K3">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge13" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;dashed=1;" edge="1" parent="1" source="I1" target="A1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
