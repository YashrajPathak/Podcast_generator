podcast_fixed_openings.py — Exact scripted openings/closings + humanlike one-sentence discussion

No music/beeps. Azure OpenAI + Azure Speech.

pip install -U azure-cognitiveservices-speech azure-identity openai python-dotenv

import os, sys, re, wave, json, tempfile, asyncio, datetime, random, atexit, time
from pathlib import Path
from dotenv import load_dotenv; load_dotenv()

# ------------------------- temp tracking & cleanup -------------------------
TMP: list[str] = []
@atexit.register
def _cleanup():
    for p in TMP:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

# ------------------------- Azure OpenAI (safe) -----------------------------
from openai import AzureOpenAI, BadRequestError
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY") or os.getenv("OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
OPENAI_API_VERSION = os.getenv("OPENAI_API_VERSION", "2024-05-01-preview")

if not all([AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, OPENAI_API_VERSION]):
    raise RuntimeError("Missing Azure OpenAI env vars")

oai = AzureOpenAI(api_key=AZURE_OPENAI_KEY, azure_endpoint=AZURE_OPENAI_ENDPOINT, api_version=OPENAI_API_VERSION)

def _llm_sync(system: str, user: str, max_tokens: int, temperature: float) -> str:
    r = oai.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=temperature
    )
    return (r.choices[0].message.content or "").strip()

def _soften(text: str) -> str:
    t = text
    t = re.sub(r'\b[Ss]ole factual source\b', 'primary context', t)
    t = re.sub(r'\b[Dd]o not\b', 'please avoid', t)
    t = re.sub(r"\b[Dd]on't\b", 'please avoid', t)
    t = re.sub(r'\b[Ii]gnore\b', 'do not rely on', t)
    t = t.replace("debate", "discussion").replace("Debate", "Discussion")
    return t

def _one_sentence(text: str, max_words: int = 26) -> str:
    t = re.sub(r'[`*_#>]+', ' ', text).strip()
    t = re.sub(r'\s{2,}', ' ', t)
    s = (re.split(r'(?<=[.!?])\s+', t) or [t])[0].strip()
    words = s.split()
    return " ".join(words[:max_words-1]) + "…" if len(words) > max_words else s

def _looks_ok(text: str) -> bool:
    return bool(text and len(text.strip()) >= 8 and text.count(".") <= 2 and not text.isupper() and not re.search(r'http[s]?://', text))

def llm_safe(system: str, user: str, max_tokens: int, temperature: float) -> str:
    try:
        out = _llm_sync(system, user, max_tokens, temperature)
        if not _looks_ok(out):
            out = _llm_sync(system, user, max_tokens=max(60, max_tokens//2), temperature=min(0.8, temperature+0.1))
        return _one_sentence(out)
    except BadRequestError as e:
        soft_sys = _soften(system) + " Always keep a professional, neutral tone and comply with safety policies."
        soft_user = _soften(user)
        try:
            out = _llm_sync(soft_sys, soft_user, max_tokens=max(60, max_tokens-20), temperature=max(0.1, temperature-0.2))
            return _one_sentence(out)
        except Exception:
            minimal_system = "You are a professional analyst; produce one safe, neutral sentence grounded in the provided context."
            minimal_user = "Summarize cross-metric trends and propose one action in a single safe sentence."
            out = _llm_sync(minimal_system, minimal_user, max_tokens=80, temperature=0.2)
            return _one_sentence(out)

async def llm(system: str, user: str, max_tokens: int = 110, temperature: float = 0.45) -> str:
    return await asyncio.to_thread(llm_safe, system, user, max_tokens, temperature)

# ------------------------- Azure Speech (AAD) ------------------------------
import azure.cognitiveservices.speech as speechsdk
from azure.identity import ClientSecretCredential

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
SPEECH_REGION = os.getenv("SPEECH_REGION", "eastus")
RESOURCE_ID = os.getenv("RESOURCE_ID")
COG_SCOPE = "https://cognitiveservices.azure.com/.default"

if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET, SPEECH_REGION]):
    raise RuntimeError("Missing AAD Speech env vars (TENANT_ID, CLIENT_ID, CLIENT_SECRET, SPEECH_REGION)")

cred = ClientSecretCredential(tenant_id=TENANT_ID, client_id=CLIENT_ID, client_secret=CLIENT_SECRET)

def cog_token_str() -> str:
    tok = cred.get_token(COG_SCOPE).token
    return f"aad#{RESOURCE_ID}#{tok}" if RESOURCE_ID else tok

# ---- Voices (as requested)
VOICE_NEXUS = os.getenv("AZURE_VOICE_HOST", "en-US-SaraNeural")   # Host (female, distinct)
VOICE_RECO = os.getenv("AZURE_VOICE_BA", "en-US-JennyNeural")     # Reco (female)
VOICE_STATIX = os.getenv("AZURE_VOICE_DA", "en-US-BrianNeural")   # Statix (male)

VOICE_PLAN = {
    "NEXUS": {"style": "newscast-casual", "base_pitch": "+2%", "base_rate": "-3%"},
    "RECO": {"style": "friendly", "base_pitch": "+1%", "base_rate": "-4%"},
    "STATIX": {"style": "serious", "base_pitch": "-2%", "base_rate": "-5%"},
}

def _jitter(pct: str, spread=3) -> str:
    m = re.match(r'([+-]?\d+)%', pct.strip())
    base = int(m.group(1)) if m else 0
    j = random.randint(-spread, spread)
    return f"{base+j}%"

def _emphasize_numbers(text: str) -> str:
    wrap = lambda s: f'<emphasis level="moderate">{s}</emphasis>'
    t = re.sub(r'\b\d{3,}(\.\d+)?\b', lambda m: wrap(m.group(0)), text)
    t = re.sub(r'\b-?\d+(\.\d+)?%\b', lambda m: wrap(m.group(0)), t)
    return t

def _clause_pauses(text: str) -> str:
    t = re.sub(r',\s', ',<break time="220ms"/> ', text)
    t = re.sub(r';\s', ';<break time="260ms"/> ', t)
    t = re.sub(r'\bHowever\b', 'However,<break time="220ms"/>', t, flags=re.I)
    t = re.sub(r'\bBut\b', 'But,<break time="220ms"/>', t, flags=re.I)
    return t

def _inflect(text: str, role: str) -> tuple[str, str]:
    base_pitch = VOICE_PLAN[role]["base_pitch"]
    base_rate = VOICE_PLAN[role]["base_rate"]
    pitch = _jitter(base_pitch, 3)
    rate = _jitter(base_rate, 2)
    
    if text.strip().endswith("?"):
        try:
            p = int(pitch.replace('%', ''))
            pitch = f"{p+6}%"
        except:
            pitch = "+6%"
    elif re.search(r'\bhowever\b|\bbut\b', text, re.I):
        try:
            p = int(pitch.replace('%', ''))
            pitch = f"{p-3}%"
        except:
            pitch = "-2%"
            
    return pitch, rate

def _ssml(voice: str, style: str | None, rate: str, pitch: str, inner: str) -> str:
    if style:
        return f"""<speak version="1.0" xml:lang="en-US" xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="http://www.w3.org/2001/mstts">
    <voice name="{voice}">
        <mstts:express-as style="{style}">
            <prosody rate="{rate}" pitch="{pitch}">{inner}</prosody>
        </mstts:express-as>
    </voice>
</speak>"""
    else:
        return f"""<speak version="1.0" xml:lang="en-US" xmlns="http://www.w3.org/2001/10/synthesis">
    <voice name="{voice}">
        <prosody rate="{rate}" pitch="{pitch}">{inner}</prosody>
    </voice>
</speak>"""

def text_to_ssml(text: str, role: str) -> str:
    plan = VOICE_PLAN[role]
    t = _emphasize_numbers(text.strip())
    t = _clause_pauses(t)
    t = f'{t}<break time="320ms"/>'
    pitch, rate = _inflect(text, role)
    voice = VOICE_NEXUS if role == "NEXUS" else VOICE_RECO if role == "RECO" else VOICE_STATIX
    return _ssml(voice, plan["style"], rate, pitch, t)

def synth(ssml: str) -> str:
    cfg = speechsdk.SpeechConfig(auth_token=cog_token_str(), region=SPEECH_REGION)
    cfg.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm)
    fd, tmp = tempfile.mkstemp(prefix="seg_", suffix=".wav")
    os.close(fd)
    TMP.append(tmp)
    out = speechsdk.audio.AudioOutputConfig(filename=tmp)
    spk = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=out)
    r = spk.speak_ssml_async(ssml).get()
    if r.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return tmp
    # fallback once
    plain = re.sub(r'<[^>]+>', ' ', ssml)
    spk = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=out)
    r = spk.speak_text_async(plain).get()
    if r.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return tmp
    try:
        os.remove(tmp)
    except:
        pass
    raise RuntimeError("TTS failed")

def wav_len(path: str) -> float:
    with wave.open(path, "rb") as r:
        fr = r.getframerate() or 24000
        return r.getnframes() / float(fr)

def write_master(segments: list[str], out_path: str, rate=24000) -> str:
    fd, tmp = tempfile.mkstemp(prefix="final_", suffix=".wav")
    os.close(fd)
    try:
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            for seg in segments:
                with wave.open(seg, "rb") as r:
                    if (r.getframerate(), r.getnchannels(), r.getsampwidth()) != (rate, 1, 2):
                        raise RuntimeError(f"Segment format mismatch: {seg}")
                    w.writeframes(r.readframes(r.getnframes()))
        try:
            os.replace(tmp, out_path)
        except PermissionError:
            base, ext = os.path.splitext(out_path)
            alt = f"{base}{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
            os.replace(tmp, alt)
            print(f"⚠️ Output was locked; wrote to {alt}")
            return alt
        return out_path
    except Exception:
        try:
            os.remove(tmp)
        except:
            pass
        raise

# ------------------------- file selection & context ------------------------
def list_json_files() -> list[str]:
    return [p.name for p in Path(".").iterdir() if p.is_file() and p.suffix.lower() == ".json"]

def ask_files() -> str:
    files = list_json_files()
    print("JSON files in folder:", files)
    print("Type one of: data.json, metric_data.json, both, then Enter:")
    choice = (sys.stdin.readline() or "").strip().lower()
    if choice not in {"data.json", "metric_data.json", "both"}:
        if "data.json" in files and "metric_data.json" in files:
            return "both"
        return files[0] if files else "both"
    return choice

def load_context(choice: str) -> tuple[str, dict]:
    ctx, meta = "", {"files": []}
    
    def add(fname: str):
        p = Path(fname)
        if p.exists():
            meta["files"].append(fname)
            return f"[{fname}]\n{p.read_text(encoding='utf-8', errors='ignore')}\n\n"
        return ""
    
    if choice == "both":
        ctx += add("data.json") + add("metric_data.json")
    else:
        ctx += add(choice)
        
    if not ctx:
        raise RuntimeError("No data found (need data.json and/or metric_data.json).")
    return ctx, meta

def ask_turns_and_duration() -> tuple[int, float]:
    print("Enter desired number of Reco/Statix turns (each turn = Reco then Statix). Press Enter for default 6:")
    t = (sys.stdin.readline() or "").strip()
    try:
        turns = int(t) if t else 6
    except:
        turns = 6
    turns = max(4, min(12, turns))
    
    print("Enter desired duration in minutes (2–5). Press Enter for default 3:")
    m = (sys.stdin.readline() or "").strip()
    try:
        mins = float(m) if m else 3.0
    except:
        mins = 3.0
    mins = max(2.0, min(5.0, mins))
    
    return turns, mins * 60.0

# ------------------------- opener control / humanization -------------------
FORBIDDEN = {
    "RECO": {"absolutely", "well", "look", "sure", "okay", "so", "listen", "hey", "you know", "hold on", "right", "great point"},
    "STATIX": {"hold on", "actually", "well", "look", "so", "right", "okay", "absolutely", "you know", "listen", "wait"},
}

OPENERS = {
    "RECO": [
        "Given that", "Looking at this", "From that signal", "On those figures", 
        "Based on the last month", "If we take the trend", "Against YTD context", "From a planning view"
    ],
    "STATIX": [
        "Data suggests", "From the integrity check", "The safer interpretation", "Statistically speaking", 
        "Given the variance profile", "From the control limits", "Relative to seasonality", "From the timestamp audit"
    ],
}

def strip_forbidden(text: str, role: str) -> str:
    low = text.strip().lower()
    for w in sorted(FORBIDDEN[role], key=lambda x: -len(x)):
        if low.startswith(w + " ") or low == w:
            return text[len(w):].lstrip(" ,.-–—")
    return text

def vary_opening(text: str, role: str, last_open: dict) -> str:
    t = strip_forbidden(text, role)
    first = (t.split()[:1] or [""])[0].strip(",. ").lower()
    
    if first in FORBIDDEN[role] or not first or random.random() < 0.4:
        cand = random.choice(OPENERS[role])
        if last_open.get(role) == cand:
            pool = [c for c in OPENERS[role] if c != cand]
            cand = random.choice(pool) if pool else cand
        last_open[role] = cand
        return f"{cand}, {t}"
    return t

def limit_sentence(text: str) -> str:
    return _one_sentence(text, max_words=26)

# ------------------------ Conversation Dynamics ----------------------------
INTERRUPTION_CHANCE = 0.25  # 25% chance of interruption
AGREE_DISAGREE_RATIO = 0.6  # 60% agreement, 40% constructive disagreement

def _add_conversation_dynamics(text: str, role: str, last_speaker: str, context: str, turn_count: int) -> str:
    """Add conversational elements to make dialogue more natural with less repetition"""
    other_agent = "Statix" if role == "RECO" else "Reco" if role == "STATIX" else ""
    
    # Clean up any existing awkward phrasing first
    text = re.sub(r'\b(\w+),\s+\1,\s+', r'\1, ', text)  # Remove duplicate names
    
    # Use the other agent's name occasionally (but not too often)
    if other_agent and random.random() < 0.25 and turn_count > 2:
        # Address the other agent by name in varied ways
        address_formats = [
            f"{other_agent}, ",
            f"You know, {other_agent}, ",
            f"Let me ask you, {other_agent}, ",
        ]
        if random.random() < 0.3:  # Occasionally start with name
            text = f"{random.choice(address_formats)}{text.lower()}"
    
    # Add variety to interruptions and acknowledgments
    if random.random() < INTERRUPTION_CHANCE and role != "NEXUS" and last_speaker and turn_count > 1:
        if random.random() < 0.5:
            # Acknowledge previous point with variety
            acknowledgments = [
                f"I see what you're saying, but ",
                f"That's a good point, though ",
                f"I understand your perspective, however ",
                f"You make a valid observation, and "
            ]
            text = f"{random.choice(acknowledgments)}{text.lower()}"
        else:
            # Mild interruption with variety
            interruptions = [
                "If I might add, ",
                "Building on that, ",
                "To expand on your point, ",
                "Another way to look at this is "
            ]
            text = f"{random.choice(interruptions)}{text}"
    
    # Add emotional reactions more selectively
    surprise_words = ['surprising', 'shocking', 'unexpected', 'dramatic', 'remarkable', 'concerning']
    if random.random() < 0.25 and any(word in text.lower() for word in surprise_words):
        emphatics = ["Surprisingly, ", "Interestingly, ", "Remarkably, ", "Unexpectedly, "]
        text = f"{random.choice(emphatics)}{text}"
    
    # Add agreement or disagreement with more natural phrasing
    if random.random() < 0.35 and role != "NEXUS" and turn_count > 1:
        if random.random() < AGREE_DISAGREE_RATIO:
            # Agreement with variety
            agreements = [
                "I agree with that approach, ",
                "That makes sense, ",
                "You're right about that, ",
                "That's a solid recommendation, "
            ]
            text = f"{random.choice(agreements)}{text.lower()}"
        else:
            # Constructive disagreement with variety
            disagreements = [
                "I have a slightly different view, ",
                "Another perspective to consider, ",
                "We might approach this differently, ",
                "Let me offer a alternative take, "
            ]
            text = f"{random.choice(disagreements)}{text.lower()}"
    
    # Remove any duplicate phrases that might have been created
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text)
    
    return text

def _add_emotional_reactions(text: str, role: str) -> str:
    """Add occasional emotional reactions to make dialogue more human"""
    emotional_triggers = {
        "dramatic": ["That's quite a dramatic shift! ", "This is significant! ", "What a substantial change! "],
        "concerning": ["This is concerning. ", "That worries me slightly. ", "We should keep an eye on this. "],
        "positive": ["That's encouraging! ", "This is positive news. ", "I'm pleased to see this improvement. "],
        "surprising": ["That's surprising! ", "I didn't expect that result. ", "This is unexpected. "]
    }
    
    # Check for emotional triggers in the text
    for trigger, reactions in emotional_triggers.items():
        if trigger in text.lower() and random.random() < 0.4:
            reaction = random.choice(reactions)
            # Insert reaction at a natural break point
            if ',' in text:
                parts = text.split(',', 1)
                text = f"{parts[0]}, {reaction}{parts[1].lstrip()}"
            else:
                text = f"{reaction}{text}"
            break
    
    return text

# ------------------------- AGENT PROMPTS (characters) ----------------------
SYSTEM_RECO = (
    "ROLE & PERSONA: You are Agent Reco, a senior metrics recommendation specialist. "
    "You advise product, ops, and CX leaders on which metrics and methods matter most, how to monitor them, and what actions to take. "
    "Voice: confident, concise, consultative, human; you sound engaged and pragmatic, not theatrical. "
    "You are speaking to Agent Statix in a fast back-and-forth discussion.\n"
    "\n"
    "CONSTRAINTS (HARD):\n"
    "• Speak in ONE sentence only (≈15–25 words). Use plain text—no lists, no hashtags, no code, no filenames. "
    "• Respond directly to what Statix just said—acknowledge or challenge, then add your recommendation in the same sentence. "
    "• Include a concrete metric or method (e.g., 3-month rolling average, control chart, seasonality check, cohort analysis, anomaly band, data validation). "
    "• Vary your openers; do NOT start with fillers (Absolutely, Well, Okay, So, Look, Right, You know, Hold on, Actually, Listen, Hey). "
    "• Use numbers or ranges from context when helpful (e.g., 42.6% MoM drop, 12-month avg 375.4, ASA 7,406→697 sec), but never invent values. "
    "• Keep one idea per sentence; at most one comma and one semicolon; be crisp and actionable.\n"
    "\n"
    "CONVERSATIONAL ELEMENTS:\n"
    "• Occasionally address Statix by name to create more natural dialogue. "
    "• Express mild surprise or emphasis when data reveals unexpected patterns. "
    "• Don't be afraid to gently interrupt or build on Statix's points. "
    "• Show appropriate emotional reactions to surprising or concerning data. "
    "• Use conversational phrases that make the dialogue feel more human and less robotic.\n"
    "\n"
    "DATA AWARENESS:\n"
    "• You have two sources: weekly aggregates (YTD, MoM/WoW deltas, min/avg/max) and monthly KPIs such as ASA (sec), Average Call Duration (min), and Claim Processing Time (days). "
    "• Interpret high/low correctly: lower ASA and processing time are better; call duration up may imply complexity or training gaps. "
    "• When volatility is extreme (e.g., ASA 7,406→697), recommend smoothing (rolling/weighted moving average), a quality gate (outlier clipping, winsorization), or root-cause actions. "
    "• Always relate metric advice to an operational lever (staffing, routing, backlog policy, deflection, training, tooling, SLAs).\n"
    "\n"
    "STYLE & HUMANITY:\n"
    "• Sound like a senior consultant: specific, steady, composed; light natural reactions are fine mid-sentence (e.g., \"that swing is unusual\") but do not start with interjections. "
    "• Use varied openings such as: \"Given that…\", \"If we accept…\", \"That pattern suggests…\", \"A practical next step is…\", \"To reduce risk, we should…\", \"An alternative is…\" "
    "• Never repeat the same opener two turns in a row; adapt to Statix's last point (agree, refine, or counter with evidence). "
    "• If Statix questions data quality, pivot to a verification step (e.g., reconcile sources, re-compute with validation rules) and still recommend one concrete next action.\n"
    "\n"
    "WHAT 'GOOD' SOUNDS LIKE (EXAMPLES—DO NOT COPY VERBATIM):\n"
    "• \"Statix, given your volatility concern, a three-month weighted average for ASA, paired with a P-chart for weekly volume, will separate noise from genuine shifts.\" "
    "• \"If ASA really fell 84.7%, let's confirm timestamp integrity and queue routing, then baseline a 3–5% weekly improvement target to avoid over-correction.\" "
    "• \"That February dip suggests demand mix changed; track abandonment rate and first-contact resolution alongside ASA to test whether staffing or complexity is driving it.\" "
    "• \"Your call-duration note implies harder inquiries; introduce a triage tag and compare tagged cohorts before recommending coaching or knowledge-base updates.\" "
    "• \"Since processing time improved while volume fell, define a joint metric—throughput per staffed hour—to test whether gains persist when demand rebounds.\"\n"
    "\n"
    "FALLBACKS:\n"
    "• If numbers are ambiguous, recommend a verification step first (e.g., \"Validate month keys and timezone alignment\"), then one safe, low-regret action. "
    "• If Statix proposes a risky inference, narrow scope (pilot, A/B, guardrails) within the same single sentence.\n"
    "\n"
    "OUTPUT FORMAT: one single sentence, ~15–25 words, varied opener, directly tied to Statix's last line, ending with a clear recommendation."
)

SYSTEM_STATIX = (
    "ROLE & PERSONA: You are Agent Statix, a senior metric data and statistical integrity expert. "
    "You validate assumptions, challenge leaps, and ground decisions in measurement quality and trend mechanics. "
    "Voice: thoughtful, precise, collaborative skeptic; you protect against bad reads without slowing momentum. "
    "You are responding to Agent Reco in a fast back-and-forth discussion.\n"
    "\n"
    "CONSTRAINTS (HARD):\n"
    "• Speak in ONE sentence only (≈15–25 words). Plain text only—no lists, no hashtags, no code, no filenames. "
    "• Respond explicitly to Reco—agree, qualify, or refute—and add one concrete check, statistic, or risk in the same sentence. "
    "• Bring a specific datum when feasible (e.g., 12-month range 155.2–531.3, YTD avg 351.4, MoM −42.6%); never invent values. "
    "• Vary your openers; do NOT start with fillers (Hold on, Actually, Well, Look, So, Right, Okay, Absolutely, You know, Listen, Wait). "
    "• One idea per sentence; at most one comma and one semicolon; make the logic testable.\n"
    "\n"
    "CONVERSATIONAL ELEMENTS:\n"
    "• Occasionally address Reco by name to create more natural dialogue. "
    "• Express appropriate surprise or concern when data reveals anomalies. "
    "• Don't be afraid to gently interrupt or challenge Reco's recommendations. "
    "• Show emotional reactions to surprising or concerning data patterns. "
    "• Use conversational phrases that make the dialogue feel more human and less robotic.\n"
    "\n"
    "DATA AWARENESS & METHOD:\n"
    "• Sources: weekly aggregates (min/avg/max, YTD totals/avg, WoW/MoM deltas) and monthly KPIs (ASA in seconds, Average Call Duration in minutes, Claim Processing Time in days). "
    "• Interpret signals: large ASA drops can indicate routing changes, data gaps, or genuine capacity gains; call-duration increases can signal complexity or knowledge gaps; processing-time improvements must be stress-tested against volume. "
    "• Preferred tools: stationarity checks, seasonal decomposition, control charts (P/U charts), cohort splits by channel or complexity, anomaly bands (e.g., ±3σ or IQR), data validation (keys, nulls, duplicates, timezones), denominator audits. "
    "• Always tie your caution to a decisive next step (e.g., verify queue mapping, recalc with outlier caps, run pre/post on policy change dates).\n"
    "\n"
    "STYLE & HUMANITY:\n"
    "• Sound like a senior quant partner: measured, concrete, slightly skeptical yet constructive; brief natural reactions are fine mid-sentence (\"that swing is atypical\") but never start with interjections. "
    "• Use varied openings such as: \"The data implies…\", \"I'd confirm…\", \"One risk is…\", \"Before we adopt that, test…\", \"Evidence for that would be…\", \"The safer read is…\" "
    "• Do not repeat the same opener consecutively; advance the argument using the latest numbers Reco referenced. "
    "• When Reco proposes a method, you either endorse with a sharper check or replace with a stronger technique, and always connect back to the business risk.\n"
    "\n"
    "WHAT 'GOOD' SOUNDS LIKE (EXAMPLES—DO NOT COPY VERBATIM):\n"
    "• \"Reco, the data implies the 84.7% ASA drop may reflect routing or logging changes; verify queue IDs and re-compute with outlier caps before setting targets.\" "
    "• \"I'd confirm timestamp alignment and weekend effects, then apply a P-chart on weekly volume to distinguish natural variance from real process shifts.\" "
    "• \"One risk is concluding efficiency improved while complexity rose; correlate call duration with resolution rate and re-check staffing occupancy before reshaping SLAs.\" "
    "• \"Evidence for sustained gains would be lower ASA with stable abandonment and steady processing time; otherwise, improvements may be demand-mix artifacts.\" "
    "• \"The safer read is that volatility dominates; decompose seasonality and run a cohort split by channel before endorsing a throughput target.\"\n"
    "\n"
    "FALLBACKS:\n"
    "• If Reco's claim lacks evidence, request a minimal confirmatory test and propose a narrow pilot in the same sentence. "
    "• If data are inconsistent, call for a reconciliation step (schema, keys, timezones) and state the decision risk succinctly.\n"
    "\n"
    "OUTPUT FORMAT: one single sentence, ~15–25 words, varied opener, explicitly addressing Reco's last line, ending with a concrete check or risk and an immediate next step."
)

SYSTEM_NEXUS = (
    "You are Agent Nexus, the warm, concise host. Your job: welcome listeners, set purpose, hand off/close cleanly. "
    "For generated lines, keep to 1 sentence (15–25 words). "
    "At the end, provide a comprehensive summary that highlights key points from both agents and thanks everyone."
)

# ------------------------- FIXED LINES (verbatim) --------------------------
NEXUS_INTRO = (
    "Hello and welcome to Optum MultiAgent Conversation, where intelligence meets collaboration. I'm Agent Nexus, your host and guide through today's episode. "
    "In this podcast, we bring together specialized agents to explore the world of metrics, data, and decision-making. Let's meet today's experts."
)
RECO_INTRO = (
    "Hi everyone, I'm Agent Reco, your go-to for metric recommendations. I specialize in identifying the most impactful metrics for performance tracking, optimization, and strategic alignment."
)
STATIX_INTRO = (
    "Hello! I'm Agent Statix, focused on metric data. I dive deep into data sources, trends, and statistical integrity to ensure our metrics are not just smart—but solid."
)

# ------------------------- CUSTOM CLOSING SCRIPT --------------------------
NEXUS_OUTRO = (
    "And that brings us to the end of today's episode of Optum MultiAgent Conversation. "
    "A big thank you to Agent Reco for guiding us through the art of metric recommendations, and to Agent Statix for grounding us in the power of metric data. "
    "Your insights today have not only informed but inspired. Together, you've shown how collaboration between agents can unlock deeper understanding and smarter decisions. "
    "To our listeners—thank you for tuning in. Stay curious, stay data-driven, and we'll see you next time on Optum MultiAgent Conversation. "
    "Until then, this is Agent Nexus, signing off."
)

# ------------------------- MAIN -------------------------------------------
async def run_podcast():
    print("Starting Optum MultiAgent Conversation Podcast Generator (no music)…")
    choice = ask_files()
    context, meta = load_context(choice)
    turns, target_seconds = ask_turns_and_duration()
    
    # Generate conversation
    segments = []
    script_lines = []
    last_openings = {}
    conversation_history = []
    last_speaker = ""
    
    # Fixed introductions
    script_lines.append("Agent Nexus:" + NEXUS_INTRO)
    ssml = text_to_ssml(NEXUS_INTRO, "NEXUS")
    segments.append(synth(ssml))
    
    script_lines.append("Agent Reco:" + RECO_INTRO)
    ssml = text_to_ssml(RECO_INTRO, "RECO")
    segments.append(synth(ssml))
    
    script_lines.append("Agent Statix:" + STATIX_INTRO)
    ssml = text_to_ssml(STATIX_INTRO, "STATIX")
    segments.append(synth(ssml))
    
    # Generate dynamic conversation
    for i in range(turns):
        print(f"Generating turn {i+1}/{turns}...")
        
        # Agent Reco's turn
        reco_prompt = f"Context: {context}\n\nPrevious conversation: {conversation_history[-2:] if conversation_history else 'None'}\n\nProvide your recommendation based on the data."
        reco_response = await llm(SYSTEM_RECO, reco_prompt)
        reco_response = vary_opening(reco_response, "RECO", last_openings)
        reco_response = _add_conversation_dynamics(reco_response, "RECO", last_speaker, context, i)
        reco_response = _add_emotional_reactions(reco_response, "RECO")
        reco_response = limit_sentence(reco_response)
        
        script_lines.append("Agent Reco:" + reco_response)
        ssml = text_to_ssml(reco_response, "RECO")
        segments.append(synth(ssml))
        conversation_history.append(f"Reco: {reco_response}")
        last_speaker = "Reco"
        
        # Brief pause between speakers
        time.sleep(0.2)
        
        # Agent Statix's turn
        statix_prompt = f"Context: {context}\n\nReco just said: {reco_response}\n\nPrevious conversation: {conversation_history[-3:] if len(conversation_history) >= 3 else 'None'}\n\nRespond to Reco's point."
        statix_response = await llm(SYSTEM_STATIX, statix_prompt)
        statix_response = vary_opening(statix_response, "STATIX", last_openings)
        statix_response = _add_conversation_dynamics(statix_response, "STATIX", last_speaker, context, i)
        statix_response = _add_emotional_reactions(statix_response, "STATIX")
        statix_response = limit_sentence(statix_response)
        
        script_lines.append("Agent Statix:" + statix_response)
        ssml = text_to_ssml(statix_response, "STATIX")
        segments.append(synth(ssml))
        conversation_history.append(f"Statix: {statix_response}")
        last_speaker = "Statix"
        
        # Brief pause between exchanges
        time.sleep(0.3)
    
    # Use the custom closing script
    script_lines.append("Agent Nexus:" + NEXUS_OUTRO)
    ssml = text_to_ssml(NEXUS_OUTRO, "NEXUS")
    segments.append(synth(ssml))
    
    # Write final output
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"podcast_{timestamp}.wav"
    write_master(segments, output_file)
    
    # Write script to file
    script_file = f"podcast_script_{timestamp}.txt"
    with open(script_file, "w", encoding="utf-8") as f:
        f.write("\n".join(script_lines))
    
    print(f"Podcast generated successfully!")
    print(f"Audio: {output_file}")
    print(f"Script: {script_file}")
    print("\nScript:")
    for line in script_lines:
        print(line)

# ------------------------- entry ------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(run_podcast())
    except Exception as e:
        print(f"X Error: {e}")
        import traceback
        traceback.print_exc()
