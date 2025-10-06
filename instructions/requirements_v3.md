# Prompt Voice Streaming Requirements (v3 Remediation Plan)

## 0. Objective
Transform baseline (non-streaming) implementation into the v2-defined streaming system by introducing concrete work packages, minimal abstractions, and measurable acceptance gates. v3 decomposes tasks to unblock incremental commits and early testability.

## 1. Work Package Overview
WP1 Core WebSocket & Session Scaffolding  
WP2 Audio Ingest (PCM, VAD, Duration Caps)  
WP3 Incremental ASR (Option A) + Partials & Finalization  
WP4 LLM Token Streaming + Memory Gating  
WP5 Phrase Aggregation + TTS Executor (Phrase WAV)  
WP6 Metrics, Backpressure, Error Codes, Structured Logs  
WP7 Cancellation, Shutdown, Config Validation  
WP8 Testing Suite & Tooling  
WP9 Documentation & Migration (file rename, README, HANDOFF)  

## 2. Deliverables per Work Package
### WP1 Core WebSocket & Session
- /ws endpoint (FastAPI WebSocket)
- SessionState dataclass/struct with Phase enum
- Control message handlers (start/stop/cancel)
- Binary vs JSON dispatch; reject binary before start
Acceptance: start -> CAPTURING transition logged; protocol violation triggers error + close.

### WP2 Audio Ingest & VAD
- AudioWorklet-ready PCM path (server consumes Int16 mono 16k)
- webrtcvad integration (30 ms frames)
- STREAM_MAX_UTTERANCE_MS enforcement + total byte cap
- Silence-based finalize trigger path (to THINKING)
Acceptance: Silent pause >= threshold finalizes even without client stop; cap triggers MAX_DURATION_EXCEEDED event.

### WP3 Incremental ASR (Option A)
- faster-whisper model load (int8 if available)
- Periodic re-decode scheduler using STREAM_PARTIAL_INTERVAL_MS
- Partial emission (whole text) diff vs last_partial_text
- Final transcript emission with t1, t2 metrics capture
Acceptance: First partial < 1.5s (median of 3 tests), partial updates overwrite cleanly, final transcript emitted once.

### WP4 LLM Streaming
- Streaming OpenAI path (if key) else fallback streaming generator
- Token events with first-token timestamp t3
- Timeout guards (first token & total)
- Memory update only after done:true and not cancelled
Acceptance: ≥5 llm_token events for multi-sentence reply; memory updated exactly once per reply.

### WP5 Phrase Aggregation + TTS
- Phrase segmentation (punctuation OR len >=60)
- ThreadPoolExecutor for pyttsx3; per-phrase WAV generation
- Emit tts_chunk (one WAV per phrase) + tts_complete
- First audio t4 metric capture
Acceptance: First tts_chunk before final llm_token done for multi-phrase replies; median first audio latency < 3.5s after final transcript (3 tests).

### WP6 Metrics, Backpressure, Error Codes
- Timestamp tracking (t0–t4) & structured JSON latency log
- Send queue length heuristic; partial suppression w/ single info event
- Unified error payload {type,error,code,message,recoverable}
- Error codes implemented: ASR_FAIL, LLM_TIMEOUT, etc.
Acceptance: Simulated slow client triggers backpressure info; latency log line printed per session.

### WP7 Cancellation, Shutdown, Config Validation
- cancel message sets cancellation_flag, aborts tasks, transitions COMPLETE
- Graceful server shutdown: open sessions get info then closed
- STREAM_* env parsing with validation (silence 300–2000 etc.)
Acceptance: Cancel before LLM completion produces no memory update and no llm_token done:true.

### WP8 Testing
- Unit:
  - test_partial_diff()
  - test_vad_finalize()
  - test_phrase_split()
  - test_cancel_behavior()
- Integration:
  - test_full_flow_simple(): synthetic PCM -> final transcript -> tokens -> tts_chunk
  - test_timeout_llm(): simulated delayed token -> LLM_TIMEOUT error
- Utility mocks for faster-whisper & LLM
Acceptance: All tests green in CI script (pytest exit code 0).

### WP9 Documentation & Migration
- Rename requiremrents_v1.md -> requirements_streaming_v1.md
- Add requirements_streaming_v2.md + v3 cross-links
- README: “Streaming Mode” section (prereqs, fallback, env vars)
- HANDOFF.md: current phase, next tasks, test results snapshot
- TROUBLESHOOTING.md (slow ASR, missing ffmpeg, no mic)
Acceptance: Docs reference correct file names; running grep for old typo returns zero hits.

## 3. Data Structures
```python
class Phase(str, Enum):
    IDLE="IDLE"; CAPTURING="CAPTURING"; THINKING="THINKING"
    RESPONDING="RESPONDING"; COMPLETE="COMPLETE"; ERROR="ERROR"

@dataclass
class SessionState:
    id: str
    phase: Phase = Phase.IDLE
    pcm_buffer: bytearray = field(default_factory=bytearray)
    last_partial_text: str = ""
    last_partial_emit_ts: float = 0.0
    speech_started: bool = False
    last_speech_frame_ts: float = 0.0
    vad_silence_ms: int = 0
    llm_task: Optional[asyncio.Task] = None
    tts_tasks: list[asyncio.Task] = field(default_factory=list)
    cancellation_flag: bool = False
    phrase_queue_len: int = 0
    metrics: dict = field(default_factory=lambda:{
        "t0_audio_start":None,"t1_first_partial":None,
        "t2_final_transcript":None,"t3_first_llm_token":None,
        "t4_first_tts_audio":None})
```

## 4. Configuration Defaults
| Var | Default |
|-----|---------|
| STREAM_MODEL_WHISPER | small-int8 |
| STREAM_VAD_SILENCE_MS | 700 |
| STREAM_PARTIAL_INTERVAL_MS | 1000 |
| STREAM_MAX_UTTERANCE_MS | 30000 |
| STREAM_MAX_PENDING_PHRASES | 5 |
| STREAM_LOG_LATENCY | 1 |
| STREAM_LLM_TIMEOUT_S | 30 |
| STREAM_TTS_TIMEOUT_S | 10 |

Validation failure -> startup abort with clear stderr.

## 5. Error Handling Matrix
| Scenario | Code | Recoverable |
|----------|------|-------------|
| Decoder exception | ASR_FAIL | true |
| Utterance too long | MAX_DURATION_EXCEEDED | true |
| No token before timeout | LLM_TIMEOUT | true |
| TTS synthesis timeout | TTS_FAIL | true |
| Protocol misuse | PROTOCOL_VIOLATION | false |
| Cancel invoked | CANCELLED | true |
| Unexpected exception | INTERNAL | false |

## 6. Logging Schema
stdout JSON lines:
- event: "latency" (includes deltas ms)
- event: "error" (code, detail)
- event: "info" (message)
Fields: level, sid, timestamps or code, message.

## 7. Acceptance Summary (v3)
All WP acceptances satisfied + tests green + docs updated. Latency targets met (median first partial < 1500 ms; first audio < 3500 ms). Backpressure path exercised in test with artificial slow consumer.

## 8. Risks & Mitigations (Carry Forward)
- CPU load full-buffer re-decode: monitor d_decode_ms; switch to incremental segments if >1.5x realtime.
- pyttsx3 variability: if high latency, add STREAM_TTS_ENGINE=piper alternative placeholder without full integration.

## 9. Defer / Future (Beyond v3)
- Incremental decoder optimization (Option B)
- Opus/WebRTC transport
- Phrase-level chunk slicing
- Local LLM backend (ollama) integration
- Web UI for latency metrics

## 10. Implementation Order (Concrete Commits)
1. commit: ws skeleton + session + config parse
2. commit: vad + finalize + max duration
3. commit: faster-whisper + partial loop
4. commit: llm streaming + memory gating
5. commit: phrase aggregation + tts executor
6. commit: cancellation + backpressure + error codes + logs
7. commit: tests (unit + integration harness)
8. commit: documentation & rename + cleanup
