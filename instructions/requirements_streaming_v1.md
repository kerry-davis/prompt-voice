# Prompt Voice Streaming Addendum & Gap Resolution (v1)

(renamed from typo `requiremrents_v1.md`)

## 0. Purpose
Bridge current baseline implementation and the Streaming Upgrade Requirements by enumerating concrete gap closures, acceptance tests, and a phased engineering checklist.

## 1. Gap Summary (From Baseline)
| Area | Required | Current | Gap |
|------|----------|---------|-----|
| Transport | WebSocket /ws2 | None | Missing endpoint, protocol |
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

(Original contents retained; only endpoint reference updated to /ws2 and file rename note.)

*The remainder of the document matches the original addendum; see prior git history for unchanged sections.*
