# Prompt Voice Streaming Addendum & Gap Resolution (v1)

## 0. Purpose
Bridge current baseline implementation and the Streaming Upgrade Requirements by enumerating concrete gap closures, acceptance tests, and a phased engineering checklist.

## 1. Gap Summary (From Baseline)
| Area | Required | Current | Gap |
|------|----------|---------|-----|
| Transport | WebSocket /ws | None | Missing endpoint, protocol |
| Audio Ingest | 16k PCM frames | WebM blob upload | Need AudioWorklet + resample |
| VAD | webrtcvad silence | None | Add per 30 ms frame evaluation |
| Incremental ASR | Partial + final | Single full decode | Implement rolling/incremental decode |
| ASR Engine | faster-whisper int8 | openai-whisper base | Swap + model mgmt |
| Token Streaming | Incremental tokens | Full text only | Add streaming client + fallback |
| Phrase TTS | Phrase-level chunks | Full reply WAV | Segment + synth per phrase |
| Audio Out Streaming | tts_chunk events | Base64 once | Add chunk events & playback queue |
| Metrics | t0..t4 timestamps | None | Instrument client + server |
| Error Channel | {type:"error"} | HTTP error only | Standardize structured errors |
| Cancellation | cancel message | Not supported | Session cancel handling |
| Config | STREAM_* vars | Minimal env | Add documented env map |
| Memory Update | After full reply | Immediate | Gate commit until done |
| Testing | Manual only | Manual only | Scripts + minimal unit coverage |
| Docs | Streaming specifics | Not covered | Update README + HANDOFF |

## 2. Additional Functional Requirements
FR9: Server must reject audio frames received before a start message with an error event.
FR10: Server must close session on protocol violation (unknown JSON type) after sending error.
FR11: Max utterance raw PCM duration configurable (default 30s); exceed → emit error and finalize.
FR12: Provide fallback MediaRecorder non-stream mode if AudioWorklet unsupported (feature detect).
FR13: Support graceful shutdown: open sessions send info + close when server stopping.
FR14: TTS phrase queue must never exceed configurable max pending phrases (default 5) to bound memory.

## 3. Additional Non-Functional Requirements
- Real-Time Factor (RTF) target for ASR (faster-whisper small-int8) <= 0.8 on 4-core CPU.
- Memory usage for a single live session < 500 MB.
- WebSocket backpressure: if send queue > N (e.g. 50 messages), throttle partial transcript emission cadence (skip frames) and emit info.

## 4. Detailed Components

### 4.1 Protocol States
States: IDLE -> CAPTURING -> THINKING (ASR final obtained, LLM active) -> RESPONDING (TTS streaming) -> COMPLETE
Illegal transitions produce error event + optional close.

### 4.2 WebSocket Message Validation
- JSON control messages must contain 'type'.
- Binary frames interpreted only in CAPTURING.
- Oversized binary (>64 KB/frame) triggers error (prevent latency spikes).

### 4.3 Incremental ASR Strategy
Option A (Simpler First):
- Accumulate PCM.
- Every STREAM_PARTIAL_INTERVAL_MS run transcribe() on full buffer; diff text vs last partial; send new suffix.
Tradeoff: O(n) repeat cost grows; acceptable short utterances.

Option B (Later Optimization):
- Maintain last decoded segment end timestamp; transcribe tail window only (last ~12 s) and append new tokens.

Phase 1 implements Option A; note performance risk beyond 15 s speech.

### 4.4 Silence Detection
- Frame size: 30 ms (480 samples @16kHz mono Int16).
- Maintain sliding counter of consecutive non-speech frames.
- Silence threshold STREAM_VAD_SILENCE_MS / 30 => required frames.
- On threshold and >= 1 speech segment captured: finalize.

### 4.5 LLM Streaming
- If OPENAI_API_KEY set: use streaming API; fallback synthetic generator (split full completion with small sleep).
- Timeout safeguards: If no token in 5s, emit error + abort streaming.

### 4.6 Phrase Aggregation
Rules:
- Terminate phrase on punctuation [.?!] OR length >= 60 chars OR token gap >= 700 ms.
- Each phrase enqueued for TTS; phrase seq increments.

### 4.7 TTS Execution
- ThreadPoolExecutor(max_workers=2).
- pyttsx3 call wrapped with per-phrase timeout (e.g. 10s); overrun emits error and skips phrase.
- Convert synthesized WAV to raw small chunks? (DEFER: send whole phrase WAV initially; chunking inside phrase optional later).

### 4.8 Metrics Emission
Server logs (structured JSON):
{event:"latency", session_id, t_first_partial_ms, t_final_transcript_ms, t_first_token_ms, t_first_audio_ms, total_reply_ms}
Client console markers for correlation.

### 4.9 Configuration (Expanded)
| Variable | Default | Description |
|----------|---------|-------------|
| STREAM_MODEL_WHISPER | small-int8 | faster-whisper model |
| STREAM_VAD_SILENCE_MS | 700 | Silence gap to finalize |
| STREAM_PARTIAL_INTERVAL_MS | 1000 | Partial transcript cadence |
| STREAM_MAX_UTTERANCE_MS | 30000 | Max capture duration |
| STREAM_MAX_PENDING_PHRASES | 5 | Phrase backlog safety |
| STREAM_LOG_LATENCY | 1 | Enable latency logs |
| STREAM_LLM_TIMEOUT_S | 30 | LLM stream overall timeout |
| STREAM_TTS_TIMEOUT_S | 10 | Per-phrase synthesis limit |

### 4.10 Error Codes
- ASR_TIMEOUT
- ASR_FAIL
- LLM_TIMEOUT
- LLM_FAIL
- TTS_FAIL
- PROTOCOL_VIOLATION
- MAX_DURATION_EXCEEDED
- INTERNAL

Payload: {type:"error", code, message, recoverable:bool}

## 5. Incremental Implementation Checklist

Phase 1 (ASR Partials):
[ ] Add dependencies: faster-whisper, webrtcvad
[ ] Implement /ws endpoint + session state
[ ] Client AudioWorklet capture + PCM send
[ ] Partial transcript diff logic
[ ] Silence detection finalization
[ ] Error + info message scaffolding

Phase 2 (LLM Tokens):
[ ] Streaming call (OpenAI / fallback)
[ ] Token forwarding events
[ ] Memory gating (update after done)
[ ] Timeout & cancellation

Phase 3 (Phrase TTS Streaming):
[ ] Phrase aggregator
[ ] Threaded TTS synthesis per phrase
[ ] tts_chunk / tts_phrase_done events
[ ] Early playback queue (client)

Phase 4 (Metrics & Hardening):
[ ] Latency timestamps client/server
[ ] Config env parsing + README update
[ ] Error code taxonomy
[ ] Graceful shutdown broadcast
[ ] Backpressure (skip partials if queue large)

Phase 5 (Tests / Docs):
[ ] Unit: diff partial builder
[ ] Unit: phrase aggregator boundaries
[ ] Mock LLM streamer ordering test
[ ] Integration: simulated PCM -> final transcript path
[ ] README + HANDOFF updates
[ ] Add troubleshooting section

## 6. Acceptance (Addendum-Specific)
- Protocol violations elicit structured error then close.
- Backpressure test: artificially slow client consumption still receives at least every 3rd partial (throttled).
- Timeout tests produce appropriate error codes.

## 7. Deferred (Document for Future)
- True incremental decoder reuse (Option B).
- Per-token TTS phoneme alignment.
- Opus compression for network efficiency.
- Multi-session concurrency scaling.

## 8. Risks Added by Streaming
- AudioWorklet browser compatibility (fallback required).
- Increased CPU from repeated full-buffer transcribes in Option A (monitor; optimize if RTF > 1.2).
- Thread contention pyttsx3 (consider piper if latency high).

## 9. Exit Criteria for Streaming MVP
All original streaming acceptance criteria + Addendum FR9–FR14 satisfied; latency metrics show first partial < 1.5s, first audio < 3.5s median across 5 test utterances (3–8 sec speech).

## 10. Documentation Update Tasks
[ ] Update README (Streaming Quick Start, requirements)
[ ] Add TROUBLESHOOTING.md (mic issues, model slow)
[ ] Add HANDOFF.md current status after Phase 2
[ ] Changelog entry: Introduced streaming pipeline (phases 1–2)
