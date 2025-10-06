# Prompt Voice Streaming API (C1–C12 Complete)

Real-time voice streaming API with WebSocket support for ASR → LLM → TTS pipeline. All planned commits (C1–C12) implemented: partial & final ASR, token + phrase streaming, backpressure, structured metrics, model load failure handling, graceful shutdown broadcast, and resource cleanup.

## Overview

This is the complete Phase 5 implementation of the Prompt Voice Streaming system, providing a full end-to-end voice interaction pipeline with WebSocket-based real-time communication, incremental ASR transcription, LLM streaming responses, and phrase-based TTS synthesis.

## Features

### Phase 1 - WebSocket ASR Setup ✅
- **WebSocket Endpoint**: `/ws2` (modular handler). Legacy prototype remains at `/ws`.
- **Binary PCM Support**: Accepts raw 16-bit PCM mono audio at 16kHz
- **JSON Control Messages**: Handles start/stop/cancel commands
- **Session Management**: Maintains PCM buffers and VAD state per connection
- **Error Handling**: Robust error reporting with connection preservation
- **Environment Configuration**: Configurable streaming settings

### Phase 2 - LLM Token Streaming ✅
- **OpenAI API Integration**: GPT model support with streaming responses
- **Local Fallback Mode**: Alternative LLM service for testing
- **Conversation Memory**: Context management with memory gating
- **Timeout Handling**: 5-second token timeout and configurable overall timeout
- **Cancellation Support**: Proper abort of streaming operations

### Phase 3 - Incremental TTS Audio ✅
- **Phrase-Based Synthesis**: Aggregates LLM tokens into speech phrases
- **Threaded Processing**: Concurrent TTS synthesis with ThreadPoolExecutor
- **Base64 Audio Chunks**: WAV audio encoded for WebSocket transmission
- **Playback Queue Management**: Ordered audio chunk playback on client
- **Volume Control**: Adjustable TTS playback volume

### Phase 4 - Performance & Metrics ✅
- **Latency Tracking**: End-to-end latency measurement (t0-t4 timestamps)
- **Real-time Factor (RTF)**: Audio processing efficiency metrics
- **Backpressure Handling**: WebSocket queue management with frame skipping
- **Structured Logging**: JSON-formatted metrics for monitoring
- **Graceful Shutdown**: Session cleanup with notification

### Phase 5 - Tests & Docs ✅
- **Unit Tests**: VAD, partial scheduler, config validation, phrase aggregation, LLM fallback
- **Integration (Initial)**: Core end-to-end flow (extend as needed for multi-utterance, cancellation timing)
- **Hardening (C12)**: Graceful shutdown broadcast + model load failure guard + TTS resource release
- **Documentation**: Finalized post-hardening

## Installation

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment** (optional)
   ```bash
   cp .env .env.local
   # Edit .env.local with your settings
   ```

3. **Run the Application**
   ```bash
   python app.py
   ```

   Or with uvicorn directly:
   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8000 --reload
   ```

## API Endpoints

### WebSocket: `/ws`

#### Client → Server Messages

**Start Session**
```json
{
  "type": "start",
  "sample_rate": 16000
}
```

**Stop Session**
```json
{
  "type": "stop"
}
```

**Cancel Session**
```json
{
  "type": "cancel"
}
```

**Audio Data**
- Binary PCM frames (16-bit LE, mono, 16kHz)
- Frame size: ~5120 samples (~320ms)

#### Server → Client Messages

**Partial Transcript** (Phase 1)
```json
{
  "type": "partial_transcript",
  "text": "Hello, this is a"
}
```

**Final Transcript** (Phase 1)
```json
{
  "type": "final_transcript",
  "text": "Hello, this is a complete sentence.",
  "id": "transcript_123_456"
}
```

**LLM Token** (Phase 2)
```json
{
  "type": "llm_token",
  "text": "I'm responding",
  "done": false
}
```

**TTS Audio Chunk** (Phase 3)
```json
{
  "type": "tts_chunk",
  "seq": 1,
  "audio_b64": "UklGRnoGAABXQVZFZm10IAAAA...",
  "mime": "audio/wav"
}
```

**TTS Phrase Complete** (Phase 3)
```json
{
  "type": "tts_phrase_done",
  "seq": 1
}
```

**TTS Complete** (Phase 3)
```json
{
  "type": "tts_complete"
}
```

**Error Message** (All Phases)
```json
{
  "type": "error",
  "code": "ASR_TIMEOUT",
  "message": "Error description",
  "recoverable": true
}
```

**Info Message** (All Phases)
```json
{
  "type": "info",
  "message": "Status update"
}
```

### HTTP: `/api/stream_config`
Returns current validated streaming configuration.

Example response:
```json
{
  "model_whisper": "small-int8",
  "vad_silence_ms": 700,
  "partial_interval_ms": 1000,
  "max_utterance_ms": 30000,
  "max_pending_phrases": 5,
  "log_latency": true,
  "llm_timeout_s": 30,
  "tts_timeout_s": 10,
  "enabled": true
}
```

## Configuration

Environment variables (see `.env` file):

### ASR Settings (Phase 1)
| Variable | Default | Description |
|----------|---------|-------------|
| `STREAM_MODEL_WHISPER` | `small-int8` | Whisper model for ASR |
| `STREAM_VAD_SILENCE_MS` | `700` | Silence timeout for final transcript |
| `STREAM_PARTIAL_INTERVAL_MS` | `1000` | Interval for partial transcripts |
| `STREAM_PCM_CHUNK_SIZE` | `5120` | Expected PCM chunk size |

### Backpressure Settings (Phase 4)
| Variable | Default | Description |
|----------|---------|-------------|
| `STREAM_BACKPRESSURE_THRESHOLD` | `50` | WebSocket queue size before throttling |
| `STREAM_BACKPRESSURE_SKIP_FRAMES` | `3` | Skip every Nth frame during backpressure |

### LLM Settings (Phase 2)
| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | `` | OpenAI API key for LLM streaming |
| `OPENAI_MODEL` | `gpt-3.5-turbo` | OpenAI model to use |
| `OPENAI_MAX_TOKENS` | `150` | Maximum tokens per response |
| `OPENAI_TEMPERATURE` | `0.7` | Response creativity (0.0-2.0) |
| `STREAM_LLM_TIMEOUT_S` | `30` | Overall LLM streaming timeout |

### TTS Settings (Phase 3)
| Variable | Default | Description |
|----------|---------|-------------|
| `STREAM_TTS_ENGINE` | `pyttsx3` | TTS engine to use |
| `STREAM_TTS_MAX_PHRASE_LENGTH` | `60` | Maximum characters per TTS phrase |
| `STREAM_TTS_THREAD_WORKERS` | `2` | Number of TTS synthesis threads |
| `STREAM_TTS_TIMEOUT_S` | `10` | Per-phrase synthesis timeout |
| `STREAM_MAX_PENDING_PHRASES` | `5` | Maximum queued TTS phrases |

## Usage Example

### Basic ASR Only (Phase 1)
```javascript
// Client-side WebSocket connection
const ws = new WebSocket('ws://localhost:8000/ws2');

// Start session
ws.send(JSON.stringify({
  type: 'start',
  sample_rate: 16000
}));

// Send audio data (binary PCM)
ws.send(pcmAudioBuffer);

// Handle ASR responses
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  switch (message.type) {
    case 'partial_transcript':
      console.log('Partial:', message.text);
      break;
    case 'final_transcript':
      console.log('Final:', message.text);
      break;
    case 'error':
      console.error('Error:', message.message);
      break;
  }
};

// Stop session
ws.send(JSON.stringify({ type: 'stop' }));
```

### Complete Pipeline (Phase 5)
```javascript
// Advanced client with full ASR → LLM → TTS pipeline
class VoiceAssistantClient {
  constructor() {
  this.ws = new WebSocket('ws://localhost:8000/ws2');
    this.ttsQueue = [];
    this.isPlayingTTS = false;

    this.ws.onmessage = (event) => this.handleMessage(event);
  }

  async handleMessage(event) {
    const message = JSON.parse(event.data);

    switch (message.type) {
      case 'partial_transcript':
        console.log('🎤 ASR Partial:', message.text);
        break;

      case 'final_transcript':
        console.log('🎤 ASR Final:', message.text);
        // LLM processing starts automatically on server
        break;

      case 'llm_token':
        if (message.text) {
          process.stdout.write(message.text); // Stream LLM response
        }
        if (message.done) {
          console.log('\n🤖 LLM Complete');
        }
        break;

      case 'tts_chunk':
        // Queue TTS audio for playback
        this.queueTTS(message.seq, message.audio_b64);
        break;

      case 'tts_phrase_done':
        console.log(`🎵 TTS Phrase ${message.seq} ready`);
        break;

      case 'tts_complete':
        console.log('🎵 All TTS complete');
        break;

      case 'error':
        console.error(`❌ ${message.code}: ${message.message}`);
        break;

      case 'info':
        console.log(`ℹ️  ${message.message}`);
        break;
    }
  }

  async queueTTS(sequence, audioBase64) {
    this.ttsQueue.push({ sequence, audioBase64 });
    this.ttsQueue.sort((a, b) => a.sequence - b.sequence);

    if (!this.isPlayingTTS) {
      this.playTTSQueue();
    }
  }

  async playTTSQueue() {
    if (this.isPlayingTTS || this.ttsQueue.length === 0) return;

    this.isPlayingTTS = true;

    while (this.ttsQueue.length > 0) {
      const chunk = this.ttsQueue.shift();
      await this.playAudio(chunk.audioBase64);
      console.log(`🎵 Played TTS chunk ${chunk.sequence}`);
    }

    this.isPlayingTTS = false;
  }

  async playAudio(audioBase64) {
    // Decode and play base64 WAV audio
    const binaryString = atob(audioBase64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }

    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const audioBuffer = await audioContext.decodeAudioData(bytes.buffer);

    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);
    source.start();
  }

  startRecording() {
    // Request microphone and start AudioWorklet processing
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(stream => {
        // Set up AudioWorklet for PCM processing
        // (See public/audio-client.js for complete implementation)
        this.ws.send(JSON.stringify({
          type: 'start',
          sample_rate: 16000
        }));
      });
  }

  stopRecording() {
    this.ws.send(JSON.stringify({ type: 'stop' }));
  }
}

// Usage
const client = new VoiceAssistantClient();
client.startRecording();
```

## Architecture

### Session Management
- Each WebSocket connection gets a unique session ID
- Sessions maintain PCM buffer and VAD state
- Automatic cleanup on disconnect

### Message Flow
1. Client connects to `/ws` (alias `/ws2` retained; legacy prototype at `/ws_legacy`)
2. Client sends `start` control message
3. Client streams binary PCM data
4. Server buffers audio and processes incrementally
5. Server sends partial/final transcripts
6. Client sends `stop` to finalize

### Error Handling
- WebSocket connection errors are logged and sent to client
- Invalid messages trigger error responses
- Connection remains open after errors for recovery

## Development

### Project Structure
```
├── app.py                      # Main FastAPI application
├── requirements.txt            # Python dependencies
├── .env                        # Environment configuration
├── README.md                   # This file
├── test_streaming_comprehensive.py  # Phase 5 comprehensive tests
├── test_phase2_implementation.py    # Phase 2 validation tests
├── public/
│   ├── index.html             # Frontend demo page
│   ├── audio-client.js        # WebSocket client with TTS support
│   ├── audio-worklet.js       # AudioWorklet PCM processor
│   └── README.md              # Frontend documentation
├── instructions/
│   ├── requirements.md        # Original requirements
│   └── requirements_streaming_v1.md    # Implementation requirements (renamed from typo)
└── docs/                      # Documentation files (Phase 5)
    ├── TROUBLESHOOTING.md     # Common issues and solutions
    └── HANDOFF.md             # Implementation status
```

### Logging
Application uses structured logging with timestamps and session IDs for debugging. Phase 4 adds comprehensive latency tracking and performance metrics.

### Testing
- **Health Check**: `/health` endpoint for monitoring active sessions
- **Unit Tests**: `test_streaming_comprehensive.py` covers all streaming components
- **Integration Tests**: End-to-end pipeline validation
- **Performance Tests**: Latency and throughput measurement

Run tests:
```bash
# Run comprehensive streaming tests
python test_streaming_comprehensive.py

# Run Phase 2 validation tests
python test_phase2_implementation.py
```

## Implementation Status

### ✅ Completed Phases

**Phase 1** - WebSocket ASR Setup
- Real-time PCM audio streaming via WebSocket
- Incremental ASR with partial and final transcripts
- Voice Activity Detection (VAD) for speech segmentation
- Session management with proper state transitions

**Phase 2** - LLM Token Streaming
- OpenAI API integration with streaming responses
- Local fallback mode for testing without API keys
- Conversation memory management with context limiting
- Timeout handling and cancellation support

**Phase 3** - Incremental TTS Audio
- Phrase-based TTS synthesis with pyttsx3
- Threaded audio processing for concurrent synthesis
- Base64-encoded WAV chunks for WebSocket transmission
- Ordered playback queue management

**Phase 4** - Performance & Metrics
- End-to-end latency tracking (t0-t4 timestamps)
- Real-time Factor (RTF) calculation for processing efficiency
- Backpressure handling with frame skipping
- Structured JSON logging for monitoring and analysis

## Client Timing Instrumentation (t0–t4)

Use the following snippet to capture perceived client-side latency markers; combine with server `metrics` events for full E2E insight:

```javascript
const marks = {};
function mark(label){ if(!(label in marks)) marks[label] = performance.now(); }
const ws = new WebSocket('ws://localhost:8000/ws');
ws.onopen = () => {
  // t0 should be set when microphone capture starts (not shown here)
  ws.send(JSON.stringify({type:'start', sample_rate:16000}));
};
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  switch(m.type){
    case 'partial_transcript': mark('t1_first_partial'); break;
    case 'final_transcript': mark('t2_final_transcript'); break;
    case 'llm_token': if(!m.done) mark('t3_first_llm_token'); break;
    case 'tts_chunk': mark('t4_first_tts_audio'); break;
    case 'metrics':
      console.log('Server metrics:', m);
      console.log('Client marks (ms since navigation start):', marks);
      break;
  }
};
```

## UAT (User Acceptance Testing)

For the full acceptance test matrix and procedures see `UAT.md`.

Quick checklist:
- Start server: `python app.py`
- Connect WebSocket client to `/ws`
- Speak a 3–5s utterance and confirm: partial → final (with `id`) → tokens → TTS → metrics (t0–t4)
- Capture timing deltas (see timing snippet above)
- Exercise stop, cancel, fallback (unset `OPENAI_API_KEY`), oversized frame, and binary-before-start cases
- Confirm assistant message only persisted after `llm_token` done
- Run smoke test: `pytest tests/stream/test_latency_harness.py -q`

**Phase 5** - Testing & Documentation ✅ **COMPLETED**
- Comprehensive unit tests for all streaming components
- Integration tests for complete pipeline validation
- Edge case testing for error conditions
- Complete documentation with troubleshooting guides

## Lean ASR Service (Minimal Mode)

For lightweight transcription or rapid prototyping without LLM + TTS, a lean ASR-only FastAPI app exists under `service/`.

### Why Use It?
- Faster startup (skips LLM + TTS + phrase pipeline)
- Lower resource footprint (only Whisper + simple VAD)
- Simplified WebSocket contract (partial + final transcripts only)
- Easier to integrate into existing systems needing just streaming ASR

### Key Differences vs Full Pipeline
| Aspect | Full Pipeline | Lean Service |
|--------|---------------|--------------|
| WebSocket Path | `/ws` (and `/ws2`) | `/ws` |
| Messages Emitted | partial_transcript, final_transcript, llm_token, tts_chunk, metrics | partial_transcript, final_transcript, debug (optional) |
| Finalization Triggers | VAD silence, explicit stop, max length | Same + inactivity safeguard |
| Decode Strategy | Full incremental + phrase/LLM coupling | Sliding tail partial + async full decode on finalize |
| Config Source | `backend/stream/config.py` | `service/config.py` |
| Static UI | Reuses `public/` via `/` + `/static` | Same |

### Running the Lean Service

Use the helper script (auto venv, dependency install if needed, port conflict handling):

```bash
./run_lean.sh                 # default port 8100, model small-int8
STREAM_DEBUG=true ./run_lean.sh   # enable verbose debug events
PORT=8200 MODEL_WHISPER=base-int8 ./run_lean.sh
RELOAD=1 ./run_lean.sh           # enable auto-reload (dev only)
```

Environment variables (mapped to settings):
- `MODEL_WHISPER` -> `STREAM_MODEL_WHISPER` (default: small-int8)
- `SAVE_DIR` -> `STREAM_SAVE_DIR` (final transcript persistence)
- `STREAM_DEBUG=true` to emit structured `debug` events

### WebSocket Flow (Lean)
1. Client connects to `ws://host:8100/ws`
2. Sends `{ "type": "start", "sample_rate": 16000 }`
3. Streams 16-bit PCM mono frames (recommended: ~320ms chunks, 16000 Hz)
4. Receives `partial_transcript` updates (filtered for low-info noise)
5. Finalization occurs on: explicit `stop`, silence (VAD), inactivity timeout, or max duration
6. Receives quick `final_transcript` (fast reuse of last partial) followed by upgraded final if async decode produces refinement

### HTTP Transcription Endpoint
```
POST /transcribe  (multipart form field: file=<wav>)
Response: { "transcript": "..." }
```

### Static Test UI
Navigate to `http://localhost:8100/` after starting the lean service. Current capabilities:
- Start / Stop streaming mic audio
- View partial + final transcripts in console (UI panel coming soon)

Planned incremental frontend improvements:
- Client-side downsampling (48k → 16k) for consistent PCM payloads
- Live event log panel (partial/final/debug)
- WAV file upload form invoking `/transcribe`

### Debug Events
Enable with `STREAM_DEBUG=true`. Examples include inactivity triggers, finalize reasons, timing markers (`t_partial_first`, `t_finalize_start`, etc.). These are emitted as `{ "type": "debug", "event": "...", ... }` and are safe to ignore in production.

### Troubleshooting
| Symptom | Cause | Fix |
|---------|-------|-----|
| Empty final transcript | Audio too short or silence-only | Verify PCM data; check sample rate 16000 mono 16-bit |
| No partials | Partial interval too high / filtered | Lower `STREAM_PARTIAL_INTERVAL_MS` or relax filtering thresholds |
| Port already in use | Previous uvicorn still running | Script auto-kills matching python/uvicorn on that port |
| High latency on final | Large accumulated buffer | Shorter audio chunks (~320ms) and ensure model int8 variant |

---
If you later re-enable the full pipeline, both modes can coexist (e.g. full on 8000, lean on 8100) for comparative testing.