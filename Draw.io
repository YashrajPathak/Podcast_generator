flowchart TD

%% ===== ENTRY POINTS =====
A1[graph.py CLI _cli_main()]:::cli
A2[lkk.py CLI run_podcast()]:::cli
A3[lkk.py FastAPI Server]:::api

%% ===== CONTEXT LOADING =====
B1[graph.py _cli_choose_files()]:::process
B2[graph.py load_context()]:::process
B3[graph.py infer_topic_from_metrics()]:::process
B4[lkk.py load_context()]:::process

%% ===== GRAPH ORCHESTRATION =====
C1[graph.py generate_podcast()]:::orchestrator
C2[graph.py _build_compiled_graph()]:::orchestrator
C3[graph.py _run_with_events()]:::orchestrator
C4[graph.py LangGraph Nodes]:::state

%% ===== LKK ENGINE =====
D1[lkk.py llm()]:::llm
D2[lkk.py text_to_ssml()]:::tts
D3[lkk.py synth()]:::tts
D4[lkk.py write_master()]:::io
D5[lkk.py FastAPI endpoints]:::api

%% ===== NODES =====
E1[Nexus Intro Node]:::node
E2[Reco Intro Node]:::node
E3[Stat Intro Node]:::node
E4[Nexus Topic Intro Node]:::node
E5[Reco Turn Node]:::node
E6[Stat Turn Node]:::node
E7[Nexus Outro Node]:::node

%% ===== OUTPUTS =====
F1[Final Audio (podcast_x.wav)]:::output
F2[Final Script (txt)]:::output
F3[Graph Logs + Timeline]:::output
F4[Mermaid Visualization HTML]:::output

%% ===== STYLES =====
classDef cli fill=#3b82f6,stroke=#1e3a8a,color=white
classDef process fill=#eab308,stroke=#713f12,color=black
classDef orchestrator fill=#16a34a,stroke=#14532d,color=white
classDef state fill=#22c55e,stroke=#166534,color=white
classDef node fill=#f97316,stroke=#7c2d12,color=white
classDef llm fill=#f43f5e,stroke=#7f1d1d,color=white
classDef tts fill=#06b6d4,stroke=#164e63,color=white
classDef io fill=#a855f7,stroke=#4c1d95,color=white
classDef api fill=#0ea5e9,stroke=#0c4a6e,color=white
classDef output fill=#84cc16,stroke=#3f6212,color=black

%% ===== FLOWS =====
A1 --> B1 --> B2 --> B3 --> C1
A2 --> B4 --> D1
A3 --> D5

C1 --> C2 --> C3 --> C4

C4 --> E1 --> D2 --> D3
C4 --> E2 --> D2 --> D3
C4 --> E3 --> D2 --> D3
C4 --> E4 --> D1 --> D2 --> D3
C4 --> E5 --> D1 --> D2 --> D3
C4 --> E6 --> D1 --> D2 --> D3
C4 --> E7 --> D2 --> D3

D3 --> D4 --> F1
C1 --> F2
C3 --> F3
C2 --> F4
