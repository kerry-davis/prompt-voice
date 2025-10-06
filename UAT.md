# User Acceptance Testing (UAT) Guide

This guide defines the acceptance scope, test matrix, execution steps, measurement methods, and sign‑off checklist for the Prompt Voice Streaming API.

## 1. Scope
Validates end‑to‑end streaming experience: audio capture → partial ASR → final transcript → LLM token stream → incremental TTS playback → metrics emission, plus resilience (cancellation, recoverable errors, model failure handling) and configuration exposure.

## 2. Environment Preparation
### 2.1 Prerequisites
- Python 3.11+ (or 3.10+) recommended
- CPU with ≥4 cores (Whisper int8 small model) 
- Microphone (for manual voice tests)
- Modern Chromium/Firefox browser

### 2.2 Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest httpx
```

### 2.3 Optional Keys
Set OpenAI API key (for real LLM):
```bash
export OPENAI_API_KEY=sk-...  # optional
```
Otherwise fallback token generator is used.

### 2.4 Run Server
```bash
python app.py
# or
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## 3. Requirements Traceability Matrix
| Req (original) | Description | Verification Method |
|----------------|-------------|---------------------|
| FR1 | /ws WebSocket accepts JSON + PCM | Manual connect & send start + audio + inspect events |
| FR2 | First partial <1.5s | Manual sample (console timing) + log check |
| FR3 | Final on silence (600–800 ms) | Speak then stay silent; expect final_transcript | 
| FR4 | LLM tokens start <300 ms after final | Observe timestamp diff (metrics & console markers) |
| FR5 | TTS audio begins before full reply | Observe first `tts_chunk` before `llm_token done:true` completion | 
| FR6 | Early stop works | Send `stop` mid-speech | 
| FR7 | Memory gating after done | Inspect session messages (server log `assistant_committed`) | 
| FR8 | Error allows continuation | Force ASR_FAIL by invalid audio or mock; session remains open |
| Latency t0–t4 | Metrics event includes t0..t4 | Observe `metrics` payload |
| Fallback hint | Info message includes fallback guidance | See initial info events |

## 4. Test Cases
### 4.1 Functional
| ID | Title | Steps | Expected |
|----|-------|-------|----------|
| F01 | Basic streaming happy path | Start → speak 3–5s → silence | Partial(s), final_transcript with id, llm_token stream, tts_chunk, metrics emitted |
| F02 | Silence finalization | Speak short phrase and wait | Final within ~0.7s after end |
| F03 | Manual stop | Start → speak → send stop | Final transcript (may be partial) emitted |
| F04 | Cancellation | Start → speak → send cancel | error code CANCELLED, session complete |
| F05 | Backpressure trigger | Feed long PCM (>8s) quickly | info backpressure enter/exit; partials throttled |
| F06 | Model load failure | Temporarily rename model or unset faster-whisper import | MODEL_LOAD_FAIL on start | 
| F07 | LLM fallback | Unset OPENAI_API_KEY | Tokens from fallback phrase | 
| F08 | Memory gating | Inspect logs: assistant_committed only after llm_token done | Single assistant log entry |

### 4.2 Resilience / Error
| ID | Title | Steps | Expected |
|----|-------|-------|----------|
| R01 | Oversized frame | Send >64KB binary | error PROTOCOL_VIOLATION + close |
| R02 | Binary before start | Send PCM prior to start | error PROTOCOL_VIOLATION + close |
| R03 | ASR exception | Inject malformed audio (e.g. random noise for tiny buffer) | error ASR_FAIL then continue |

### 4.3 Metrics & Performance (Exploratory)
| ID | Metric | Method | Target |
|----|--------|--------|--------|
| P01 | Partial latency (t1 - t0) | Console markers | <1500 ms |
| P02 | First token latency (t3 - t2) | metrics event | <2500 ms post speech end |
| P03 | First audio latency (t4 - t2) | metrics event | <3000 ms post speech end |
| P04 | Decode cadence | Count partial decode runs vs speech duration | ~1 per second |

## 5. Manual Execution Procedure
1. Open browser `public/index.html` (adjust WS endpoint to `/ws` if needed). 
2. Open DevTools console and paste timing snippet from README (t0–t4 markers). 
3. Click “Start” (or equivalent) → begin speaking: “Testing streaming partial transcription.” 
4. Observe partials appear; note time to first partial. 
5. Stop speaking; measure silence to final transcript. 
6. Confirm final transcript has `id`. 
7. Watch for LLM tokens streaming; capture first token time. 
8. Observe first `tts_chunk` arrival. 
9. Inspect final `metrics` event (contains t0–t4). 
10. Repeat for cancellation (send cancel mid-stream). 
11. Repeat for fallback (unset OpenAI key). 
12. Optionally stress test by streaming long random PCM (script or repeated audio). 

## 6. Automated Smoke
Run latency harness test:
```bash
pytest tests/stream/test_latency_harness.py -q
```
(Ensures `final_transcript.id` & latency metrics structure.)

## 7. Data Capture
Record for each spoken trial:
| Trial | t0 | t1 | t2 | t3 | t4 | Notes |
|-------|----|----|----|----|----|-------|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |

Compute deltas:
- First partial: t1 - t0
- Finalization: t2 - t0
- Token start: t3 - t2
- First audio: t4 - t2

## 8. Acceptance Thresholds
A release is accepted if across 5 consecutive normal utterances (3–8s speech):
- 100%: partial_transcript before final_transcript
- 100%: final_transcript includes id
- ≥80%: first partial < 1.5s
- ≥80%: first token < 2.5s after t2
- ≥80%: first audio < 3.0s after t2
- 0 critical crashes or hangs
- Recoverable errors do not terminate session erroneously

## 9. Known Limitations
- RTF not explicitly logged (can be derived by timing decode vs audio length)
- No per-token latency distribution summarization (single first-token metric only)
- Backpressure heuristic simple (size-based; no queue depth introspection)
- TTS engine (pyttsx3) latency may vary across OS
- No automated multi-utterance conversation test yet (manual only)

## 10. Sign-off Checklist
| Item | Owner | Status |
|------|-------|--------|
| All functional FRs validated |  |  |
| Latency samples collected (≥5) |  |  |
| Thresholds met (section 8) |  |  |
| Error paths exercised (R01–R03) |  |  |
| Fallback verified |  |  |
| Memory gating confirmed |  |  |
| Docs accessible (README/HANDOFF) |  |  |
| Final approval granted |  |  |

## 11. Next Steps (Post-UAT)
- Add structured performance benchmarking script (collect median & p95 latencies)
- Consider Opus transport for bandwidth reduction
- Option B incremental decode optimization
- Additional TTS engine integration (Piper / Coqui)

---
Prepared for UAT.
