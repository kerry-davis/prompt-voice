# Prompt Voice Streaming Requirements (v2)

## 0. Purpose
Convert v1 addendum into actionable, code-oriented specifications with clarified decisions, schemas, and minimal viable implementations to de-risk Phase 1–2 quickly.

## 1. Scope (Unchanged Core)
Phases 1–3 (ASR partials, token streaming, phrase TTS) + metrics. Defer intra-phrase audio chunking and advanced decoder optimization.

## 2. Key Clarifications / Decisions Since v1
- ASR Incremental Strategy: Implement Option A with full-buffer re-decode + diff; enforce hard cap STREAM_MAX_UTTERANCE_MS (default 30s). Collect performance metrics to justify later switch.
- TTS Streaming (Initial): Per phrase emit a full WAV (no sub-chunk slicing); keep interface tts_chunk for forward compatibility (one chunk per phrase initially).
- Backpressure: Simple send-queue length threshold; if exceeded, skip emitting intermediate partials (only emit latest).
- Cancellation: Immediate transition to COMPLETE; abort outstanding LLM/TTS tasks via cooperative flags.

## 3. Data Structures (Server)
SessionState:
  id: str
  phase: Enum(IDLE|CAPTURING|THINKING|RESPONDING|COMPLETE|ERROR)
  pcm_buffer: bytearray            # Int16 LE mono 16k
  last_partial_text: str
  last_partial_emit_ts: float
  speech_started: bool
  last_speech_frame_ts: float
  vad_silence_ms: int
  llm_task: asyncio.Task|None
  tts_tasks: list[asyncio.Task]
  cancellation_flag: bool
  phrase_queue_len: int
  metrics: {t0_audio_start, t1_first_partial, t2_final_transcript, t3_first_llm_token, t4_first_tts_audio}

## 4. WebSocket Protocol (v2 Minimal)
Inbound JSON:
  start: {type:"start", sample_rate:int}
  stop:  {type:"stop"}
  cancel:{type:"cancel"}

Inbound Binary:
  Raw PCM Int16 mono frames (REQUIRED after start, only in CAPTURING)

Outbound JSON Examples:
  {"type":"partial_transcript","text":"hello wor"}
  {"type":"final_transcript","text":"hello world"}
  {"type":"llm_token","text":"Hello","done":false}
  {"type":"llm_token","done":true}
  {"type":"tts_chunk","seq":0,"audio_b64":"...","mime":"audio/wav"}
  {"type":"tts_complete"}
  {"type":"error","code":"ASR_FAIL","message":"decoder error","recoverable":true}
  {"type":"info","message":"backpressure: skipping partials"}

## 5. Error Codes (Same as v1 + Added)
- ASR_TIMEOUT
- ASR_FAIL
- LLM_TIMEOUT
- LLM_FAIL
- TTS_FAIL
- PROTOCOL_VIOLATION
- MAX_DURATION_EXCEEDED
- INTERNAL
- CANCELLED (new)

## 6. State Transitions
IDLE --start--> CAPTURING
CAPTURING --silence||stop--> THINKING
THINKING --LLM first token--> RESPONDING
RESPONDING --tts_complete--> COMPLETE
ANY --cancel--> COMPLETE
ANY error --> ERROR (socket MAY close after send)

Invalid transition => PROTOCOL_VIOLATION + close.

## 7. ASR Partial Diff Algorithm (Option A)
1. Decode full buffer every STREAM_PARTIAL_INTERVAL_MS if:
   - phase == CAPTURING
   - buffer_duration_ms >= 500
2. NewText = decoded.strip()
3. If NewText != last_partial_text:
   Emit partial of NewText (entire text, not just diff suffix).
4. On finalization, emit final_transcript with final full text.

Rationale: Simplicity > minimal bandwidth; client overwrites display.

## 8. Silence Detection (VAD)
- Frame slicing: 30 ms (480 samples, 960 bytes for Int16).
- For each incoming binary frame (aggregated): run VAD over contiguous 30 ms chunks.
- Track last time speech detected. If now - last_speech_frame_ts >= STREAM_VAD_SILENCE_MS AND speech_started => finalize.
- Start speech_started when first speech frame detected.

## 9. Finalization Conditions
Triggered by:
- VAD silence
- stop control message
- MAX_DURATION_EXCEEDED
Actions:
- Move to THINKING, stop capturing frames.
- Emit final_transcript (even if empty -> may skip LLM if empty).
- Launch LLM streaming if transcript non-empty.

## 10. LLM Streaming
OpenAI Path:
- Use client.chat.completions.create(stream=True)
- For each delta containing content, emit llm_token events; record t3 on first token.
Fallback Path:
- Generate full reply; split by space; yield tokens with asyncio.sleep(0.05)

Timeouts:
- First token: STREAM_LLM_TIMEOUT_S (half of total).
- Total: STREAM_LLM_TIMEOUT_S.

On failure or timeout -> error event LLM_FAIL / LLM_TIMEOUT.

Memory update only after llm_token done:true (and not cancelled/errored).

## 11. Phrase Aggregation (Initial Simplified)
- Accumulate streamed tokens in current_phrase buffer.
- End phrase on punctuation [.?!] OR length >= 60 chars.
- When phrase ends: enqueue TTS synthesis task producing single WAV -> emit tts_chunk with seq.
- Record t4 on first emitted tts_chunk.

## 12. TTS Synthesis
Function synthesize_phrase(text) -> wav_bytes
- Run in a ThreadPoolExecutor
- Timeout STREAM_TTS_TIMEOUT_S: on timeout emit error TTS_FAIL (recoverable) and skip phrase.
- phrase_queue_len increment/decrement; if > STREAM_MAX_PENDING_PHRASES -> emit error and cancel oldest pending phrase (policy: drop oldest).

## 13. Cancellation
- On cancel:
  - cancellation_flag = True
  - Cancel llm_task + pending TTS tasks.
  - Emit info: "cancelled"
  - Transition -> COMPLETE
  - Do not update memory with partial reply.

## 14. Backpressure
Maintain send_queue_counter (messages sent but not awaited / event loop lag).
If threshold (50) exceeded:
  - Suppress intermediate partials; allow only one partial every 3 * STREAM_PARTIAL_INTERVAL_MS.
  - Emit single info event (not spammed) when suppression starts.

## 15. Metrics Emission
On COMPLETE (non-error):
  - Compute deltas:
    d_first_partial = t1 - t0
    d_final_transcript = t2 - t0
    d_first_token = t3 - t2
    d_first_audio = t4 - t2
  - Log structured JSON to stdout.
  - (Optional future: expose /metrics internal queue)

## 16. Configuration Parsing
At startup parse environment with defaults; log chosen settings once:
  STREAM_* variables; validate numeric ranges:
    silence: 300–2000 ms
    partial interval: 250–3000 ms
    max utterance: <= 120000 ms

Invalid -> exit with error message.

## 17. Security / Robustness
- Reject start if already started.
- Reject binary frames before start.
- Hard cap per-frame size 64 KB.
- Hard cap total bytes = (STREAM_MAX_UTTERANCE_MS/1000)*16000*2 + 4% overhead; exceed -> finalize with MAX_DURATION_EXCEEDED.

## 18. Testing (Concrete)
Unit:
- test_partial_diff(): simulate successive decoded texts; ensure suppression logic.
- test_vad_silence_finalize(): feed speech frames then silence frames.
- test_phrase_aggregation(): token sequences produce expected phrase splits.
- test_cancel(): ensure no memory update after cancel.
Integration (async):
- Simulated PCM generator (sine + silence) -> expect final_transcript.
- Mock LLM streamer with delayed tokens -> measure first token metrics ordering.

## 19. Logging Format
All server logs (single line JSON):
{"level":"INFO","event":"latency","sid":"...","d_first_partial_ms":...}
{"level":"ERROR","event":"error","sid":"...","code":"ASR_FAIL","detail":"trace snippet"}

## 20. Migration / Rename
- Rename file requiremrents_v1.md -> requirements_streaming_v1.md (typo fix).
- Introduce this v2; update README to link both.

## 21. Implementation Sequence (Updated)
1. Core WS + session + config parse + start/stop/cancel handling (no ASR yet).
2. Add PCM accumulation + VAD + max duration + finalization.
3. Integrate faster-whisper decode loop + partial emission + metrics t1/t2.
4. Add LLM streaming + memory gating + metrics t3.
5. Add phrase aggregation + TTS executor + metrics t4.
6. Add cancellation, backpressure, error codes, structured logs.
7. Tests + README/HANDOFF updates + rename v1 file.

## 22. Acceptance (v2)
- All v1 acceptance + additional: cancellation leaves no llm_token done event and no memory write.
- Backpressure test passes (suppression info only once).
- Partial latency median < 1.5s on 3 sample utterances.
- Unit tests green (>= 5 core tests).
- Structured logs produced per session.

## 23. Deferred
(unchanged from v1) + plus: streaming intra-phrase audio slicing, Option B decoder.
