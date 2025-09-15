<?xml version="1.0" encoding="utf-8"?>
<!-- Podcast flow preview (save as podcast_flow.svg and open in browser) -->
<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1400" viewBox="0 0 1600 1400">
  <style>
    rect{fill:#fff;stroke:#000;stroke-width:1.6}
    ellipse{fill:#fff;stroke:#000;stroke-width:1.6}
    text{font-family:Arial, Helvetica, sans-serif; font-size:12px; fill:#000}
    .note{font-size:11px; fill:#333}
    .diamond{fill:#fff;stroke:#000;stroke-width:1.6}
    .cyl{fill:#fff;stroke:#000;stroke-width:1.6}
    .dashed{stroke-dasharray:6 6; stroke:#666; stroke-width:1.2}
    .arrow{stroke:#000; stroke-width:1.6; fill:none; marker-end:url(#a)}
  </style>

  <defs>
    <marker id="a" viewBox="0 0 10 10" refX="10" refY="5" markerUnits="strokeWidth" markerWidth="8" markerHeight="6" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#000" />
    </marker>
  </defs>

  <!-- Entrypoint -->
  <ellipse cx="800" cy="56" rx="160" ry="32"/>
  <text x="700" y="60">Entrypoint: User / Scheduler / CLI / API</text>

  <!-- API -->
  <rect x="680" y="112" width="320" height="84" rx="6"/>
  <text x="700" y="142">API / Router (graph.py CLI / lkk.py FastAPI)</text>

  <!-- Left Column -->
  <rect x="120" y="220" width="280" height="88" rx="6"/>
  <text x="140" y="250">Configuration Manager</text>
  <text x="140" y="268" class="note">env vars, VOICE_*, VOICE_PLAN</text>

  <rect x="120" y="332" width="280" height="88" rx="6"/>
  <text x="140" y="362">Context Manager</text>
  <text x="140" y="380" class="note">list_json_files(), load_context(), infer_topic</text>

  <!-- Orchestrator -->
  <rect x="400" y="220" width="480" height="120" rx="6"/>
  <text x="420" y="250">LangGraph Orchestrator (graph.py)</text>
  <text x="420" y="268" class="note">_build_compiled_graph(), generate_podcast(), _run_with_events()</text>

  <!-- Nodes -->
  <rect x="420" y="360" width="440" height="56" rx="6"/>
  <text x="440" y="388">NEXUS_INTRO (nexus_intro_node)</text>

  <rect x="420" y="432" width="440" height="56" rx="6"/>
  <text x="440" y="460">RECO_INTRO (reco_intro_node)</text>

  <rect x="420" y="504" width="440" height="56" rx="6"/>
  <text x="440" y="532">STAT_INTRO (stat_intro_node)</text>

  <rect x="420" y="576" width="520" height="80" rx="6"/>
  <text x="440" y="606">NEXUS_TOPIC_INTRO (calls generate_nexus_topic_intro())</text>

  <!-- Loop area -->
  <rect x="220" y="680" width="360" height="92" rx="6"/>
  <text x="240" y="714">RECO_TURN (reco_turn_node) → llm(SYSTEM_RECO)</text>

  <rect x="720" y="680" width="360" height="92" rx="6"/>
  <text x="740" y="714">STAT_TURN (stat_turn_node) → llm(SYSTEM_STAT)</text>

  <!-- Decision diamond -->
  <g transform="translate(520,800)">
    <polygon points="80,0 160,64 80,128 0,64" class="diamond"/>
    <text x="30" y="70">should_continue?</text>
    <text x="30" y="88" class="note">(current_turn &lt; max_turns)</text>
  </g>

  <!-- Outro -->
  <rect x="520" y="928" width="420" height="88" rx="6"/>
  <text x="540" y="968">NEXUS_OUTRO (nexus_outro_node)</text>

  <!-- Engine pipeline -->
  <rect x="1200" y="220" width="300" height="80" rx="6"/>
  <text x="1220" y="250">LLM Service (lkk.py): llm_safe(), llm()</text>

  <rect x="1200" y="320" width="300" height="72" rx="6"/>
  <text x="1220" y="350">SSML Generator: text_to_ssml(), _inflect()</text>

  <rect x="1200" y="408" width="300" height="72" rx="6"/>
  <text x="1220" y="438">TTS Synth: synth()</text>

  <rect x="1200" y="496" width="300" height="72" rx="6"/>
  <text x="1220" y="526">Audio Processor: write_master(), wav_len()</text>

  <!-- Outputs cylinder -->
  <ellipse cx="780" cy="1060" rx="260" ry="32" class="cyl"/>
  <rect x="520" y="1060" width="520" height="80" class="cyl" rx="6"/>
  <ellipse cx="780" cy="1140" rx="260" ry="32" class="cyl"/>
  <text x="560" y="1100">Outputs & Storage: podcast_YYYYMMDD.wav, podcast_script.txt, graph_convo.json, mermaid.html</text>

  <!-- Monitoring -->
  <rect x="120" y="1040" width="280" height="120" rx="6"/>
  <text x="140" y="1068">Monitoring: WebSocket timeline.jsonl, mermaid HTML</text>

  <!-- External services -->
  <rect x="1450" y="120" width="240" height="96" rx="6" class="dashed"/>
  <text x="1470" y="148">External: Azure OpenAI (LLM), Azure Speech (TTS)</text>

  <!-- Arrows -->
  <path class="arrow" d="M800,88 L800,112"/>
  <path class="arrow" d="M800,196 L800,220"/>
  <path class="arrow" d="M400,264 L520,264"/>
  <path class="arrow" d="M400,356 L420,356"/>
  <path class="arrow" d="M640,656 L500,688"/>
  <path class="arrow" d="M560,656 L720,688"/>
  <path class="arrow" d="M840,656 L940,688"/>
  <path class="arrow" d="M900,772 L680,800"/>
  <path class="arrow" d="M600,896 L520,928"/>
  <path class="arrow" d="M760,1016 L760,1040"/>

  <!-- Engine arrows -->
  <path class="arrow" d="M940,736 L1200,260"/>
  <path class="arrow" d="M980,768 L1200,340"/>
  <path class="arrow" d="M990,820 L1200,420"/>
  <path class="arrow dashed" d="M1260,240 L1470,160"/>
  <path class="arrow dashed" d="M1320,420 L1470,200"/>

  <!-- LLM pipeline arrows -->
  <path class="arrow" d="M1350,300 L1350,320"/>
  <path class="arrow" d="M1350,392 L1350,408"/>
  <path class="arrow" d="M1350,480 L1350,496"/>
  <path class="arrow dashed" d="M1350,260 L1510,160"/>

</svg>
