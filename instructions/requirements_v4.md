# Prompt Voice Streaming Requirements (v4 Focused Remediation)

## 0. Objective
Break v3 plan into atomic, independently testable increments with clear rollback, instrumentation, and acceptance gates to accelerate safe streaming adoption.

## 1. Guiding Principles
- Ship vertical slivers (observable + test) before expanding scope.
- Instrument first; optimize later.
- Fail fast with explicit error codes; never silent fail.
- Keep HTTP /api/voice path working as fallback until streaming stable.

## 2. Increment Map (Atomic Commits)
C1 Config & Enums  
C2 WebSocket skeleton + Session registry  
C3 VAD & duration guards (stub ASR)  
C4 Faster-whisper integration (full-buffer) + partial loop  
C5 Partial diff logic + backpressure guard  
C6 Finalization path + metrics (t0,t1,t2)  
C7 LLM streaming + timeouts + memory gating (t3)  
C8 Phrase aggregation + TTS executor (t4)  
C9 Cancellation + error taxonomy + structured logs  
C10 Tests unit + integration harness  
C11 Docs + file renames + fallback notes  
C12 Hardening (graceful shutdown, resource cleanup)

## 3. Acceptance Gates per Commit
| Commit | Gate |
|--------|------|
| C2 | Can open /ws, receive start, reject binary pre-start |
| C3 | Silence timeout emits finalize stub event (no ASR) |
| C4 | Full transcript produced after speech, no crash |
| C5 | Partials update (>=2) for 4s utterance |
| C6 | Latency log includes t0,t1,t2 |
| C7 | ≥5 llm_token events; memory updated once |
| C8 | ≥1 tts_chunk emitted before llm_token done (multi-phrase) |
| C9 | cancel produces CANCELLED error & no memory update |
| C10 | pytest green (≥6 tests) |
| C11 | README shows streaming steps; typo file removed |
| C12 | Server shutdown closes active sessions cleanly |

## 4. Data & State (Refined)
Add per-session counters:
- decode_runs
- last_decode_ms
- rtf_estimate (decode_ms / audio_ms)
Add flags:
- backpressure_active (bool)

## 5. Configuration (Parsing Layer)
Required loader: load_stream_config()
Validation:
- partial_interval ∈ [250, 3000]
- silence_ms ∈ [300, 2000]
- max_utterance_ms ≤ 120000
On invalid: log error JSON and exit(1).
Expose config snapshot via /api/stream_config (read-only) for debugging.

## 6. WebSocket Protocol (v4 Clarifications)
Inbound control additions:
- {"type":"stop"} (user manually ends capture)
Outbound additions:
- {"type":"finalize_pending"} just before THINKING (diagnostic)
Versioning:
- First server message after start: {"type":"protocol","version":1}

## 7. Error Codes (Extended)
Add:
- BACKPRESSURE (non-fatal info escalation)
- UNSUPPORTED_SAMPLE_RATE (recoverable)
- MODEL_LOAD_FAIL (fatal)
Severity tiers:
- fatal_close: PROTOCOL_VIOLATION, INTERNAL, MODEL_LOAD_FAIL
- recoverable_continue: ASR_FAIL, LLM_TIMEOUT, TTS_FAIL, MAX_DURATION_EXCEEDED, BACKPRESSURE

## 8. Logging (Structured JSON)
Mandatory fields:
- ts (ISO8601), level, sid, event
Events:
- event="phase", phase="CAPTURING"
- event="metrics", d_first_partial_ms, etc.
- event="decode", audio_ms, decode_ms, rtf
- event="error", code, recoverable
Sampling:
- decode event every run OR if rtf changes >20% from prior.

## 9. ASR Partial Strategy Guardrails
- Minimum buffer growth between re-decodes: 200 ms; if not reached skip cycle.
- Cap decode frequency to prevent overlap (skip if previous decode still running— enforce single-thread decode lock).
- If rtf_estimate > 1.5 for 2 consecutive decodes: emit info advising model downsize.

## 10. Backpressure Policy
- Maintain outbound_queue_len (approx by counting pending awaits).
- Threshold: 50 => set backpressure_active, emit single info.
- While active: only emit partials if (now - last_partial_emit) > 3 * partial_interval.
- Clear condition: queue_len < 25 => emit info cleared.

## 11. LLM Streaming
Timeout Strategy:
- Start overall timer at first request.
- If no token in first_token_timeout (min(5s, STREAM_LLM_TIMEOUT_S/2)) => LLM_TIMEOUT.
- Per-token guard: if gap > 4s mid-stream => LLM_TIMEOUT.
Recovery: emit error and transition to COMPLETE (skip TTS).

## 12. Phrase Aggregation
Tokenizer: naive split on whitespace; append tokens to phrase buffer.
Phrase end conditions:
- punctuation at end
- char_len >= 60
- last token gap >= 700 ms (timestamp per token)
Emit phrase immediately to TTS executor.

## 13. TTS Executor
Queue: FIFO
ThreadPoolExecutor(max_workers=2)
Timeout per phrase: STREAM_TTS_TIMEOUT_S
If phrase_queue_len > STREAM_MAX_PENDING_PHRASES:
- Drop oldest queued (not running) phrases, emit BACKPRESSURE info.

## 14. Cancellation Semantics
On cancel:
- Set cancellation_flag
- Cancel llm_task (if running)
- Mark all queued TTS tasks to skip emission (wrap future results check)
- Emit {"type":"error","code":"CANCELLED","recoverable":true}
- Phase -> COMPLETE

## 15. Shutdown Handling
Hook on FastAPI lifespan shutdown:
- Broadcast info shutdown to open sessions
- Wait max 1s for tasks; force close sockets

## 16. Testing Enhancements
Fixtures:
- mock_faster_whisper (returns scripted transcripts per call)
- mock_stream_llm (async generator)
Time control: use freezegun or manual timestamp injection for deterministic metrics tests.

Core tests (add):
- test_backpressure_activation()
- test_llm_timeout_no_tokens()
- test_rtf_advisory_message()
Coverage target: statements ≥ 70% in streaming modules.

## 17. File / Module Layout
backend/stream/
  config.py
  session.py
  websocket.py
  asr.py
  vad.py
  llm_stream.py
  phrase_tts.py
  errors.py
  metrics.py
tests/stream/
  test_config.py
  test_vad.py
  test_partial.py
  test_llm_stream.py
  test_phrase_tts.py
  test_flow_integration.py

## 18. Rollback Strategy
- Retain HTTP /api/voice path during C1–C8.
- Feature flag STREAMING_ENABLED (default on) to disable /ws without removing code.
- If fatal model load fail: disable streaming automatically and log MODEL_LOAD_FAIL.

## 19. Manual Validation Script
Provide scripts/manual_ws_client.py to:
- Open WS, send start, feed PCM sine + silence, print events, measure ordering.

## 20. Acceptance (v4)
- All v3 acceptance + new: backpressure activation test, rtf advisory, graceful shutdown verified (no dangling tasks).
- Fallback path remains functional.
- No fatal_close errors during 5 sequential interactive tests.

## 21. Deferred (Still)
- Incremental tail-only decoding (Option B)
- Opus transport
- Multi-session scaling metrics
- Alternative TTS engines
- UI token-by-token highlight
