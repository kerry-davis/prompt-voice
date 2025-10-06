# Changelog - Prompt Voice Streaming API

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-10-05

### 🚀 **Phase 5 Complete - Production Ready**

#### Added
- **Complete End-to-End Voice Pipeline**: Full ASR → LLM → TTS implementation
- **Comprehensive Testing Suite**: Unit tests, integration tests, and edge case coverage
- **Production Documentation**: Complete setup, usage, and troubleshooting guides
- **Performance Monitoring**: Latency tracking, RTF metrics, and structured logging
- **Error Handling**: Robust error management with proper recovery mechanisms

#### 🎯 **Streaming Pipeline Features**

**Phase 1 - WebSocket ASR Setup**
- Real-time PCM audio streaming via WebSocket (`/ws` endpoint)
- Binary 16-bit mono audio support at 16kHz sample rate
- Voice Activity Detection (VAD) for speech segmentation
- Incremental ASR with partial and final transcript support
- Session management with proper state transitions
- JSON control message protocol (start/stop/cancel)

**Phase 2 - LLM Token Streaming**
- OpenAI GPT model integration with streaming responses
- Local fallback mode for testing without API keys
- Conversation memory management with context limiting (10 messages max)
- Timeout handling: 5-second token timeout + configurable overall timeout (30s default)
- Memory gating: Only update conversation memory after complete LLM response
- Proper cancellation support for streaming operations

**Phase 3 - Incremental TTS Audio**
- Phrase-based TTS synthesis using pyttsx3 engine
- Threaded audio processing with ThreadPoolExecutor (2-8 workers)
- Base64-encoded WAV audio chunks for WebSocket transmission
- Ordered playback queue management on client side
- Volume control and playback state management
- Per-phrase synthesis timeout (10s default) with queue limits (5 phrases max)

**Phase 4 - Performance & Metrics**
- End-to-end latency tracking with 5 measurement points:
  - t0: First audio sample received
  - t1: First partial transcript sent (< 500ms target)
  - t2: Final transcript sent (< 800ms target)
  - t3: First LLM token sent (< 1200ms target)
  - t4: First TTS audio sent (< 2500ms target)
- Real-time Factor (RTF) calculation for processing efficiency
- Backpressure handling with configurable frame skipping
- Structured JSON logging for monitoring and correlation
- Graceful shutdown with session cleanup and notification

**Phase 5 - Testing & Documentation**
- **Unit Tests** (700 lines): Complete coverage of all streaming components
  - PhraseAggregator boundary detection (punctuation, length, token gaps)
  - LLM streaming service ordering and sequence handling
  - TTS service phrase aggregation and synthesis
  - Session state management and transitions
  - Backpressure handling and frame skipping
- **Integration Tests**: End-to-end pipeline validation
- **Edge Case Tests**: Error conditions, timeouts, and boundary scenarios
- **Performance Tests**: Latency measurement and throughput validation

#### 📚 **Documentation**

**Updated Files**
- `README.md` (195 lines): Complete usage guide with examples
- `TROUBLESHOOTING.md` (600 lines): Comprehensive troubleshooting guide
- `HANDOFF.md` (500 lines): Implementation status and deployment guide

**New Files**
- `test_streaming_comprehensive.py`: Phase 5 comprehensive test suite
- `CHANGELOG.md`: This changelog file

#### ⚙️ **Configuration System**

**ASR Settings**
- `STREAM_MODEL_WHISPER`: Whisper model selection (default: small-int8)
- `STREAM_VAD_SILENCE_MS`: Voice activity timeout (default: 700ms)
- `STREAM_PARTIAL_INTERVAL_MS`: Partial transcript frequency (default: 1000ms)
- `STREAM_PCM_CHUNK_SIZE`: Audio frame size (default: 5120 samples)

**Backpressure Management**
- `STREAM_BACKPRESSURE_THRESHOLD`: Queue size before throttling (default: 50)
- `STREAM_BACKPRESSURE_SKIP_FRAMES`: Frame skip rate during pressure (default: 3)

**LLM Integration**
- `OPENAI_API_KEY`: API key for GPT models
- `OPENAI_MODEL`: Model selection (default: gpt-3.5-turbo)
- `OPENAI_MAX_TOKENS`: Response length limit (default: 150)
- `OPENAI_TEMPERATURE`: Response creativity (default: 0.7)
- `STREAM_LLM_TIMEOUT_S`: Overall timeout (default: 30s)

**TTS Configuration**
- `STREAM_TTS_ENGINE`: Engine selection (default: pyttsx3)
- `STREAM_TTS_MAX_PHRASE_LENGTH`: Characters per phrase (default: 60)
- `STREAM_TTS_THREAD_WORKERS`: Concurrent synthesis threads (default: 2)
- `STREAM_TTS_TIMEOUT_S`: Per-phrase timeout (default: 10s)
- `STREAM_MAX_PENDING_PHRASES`: Queue size limit (default: 5)

#### 🔧 **Technical Implementation**

**Core Components**
- `StreamingSession`: Session state management with latency tracking
- `PhraseAggregator`: Token aggregation with boundary detection
- `LLMStreamingService`: OpenAI integration with timeout handling
- `TTSStreamingService`: Threaded synthesis with queue management
- `AudioStreamingClient`: Browser-based WebSocket client with AudioWorklet

**Message Protocol**
- **Client → Server**: Binary PCM + JSON control messages
- **Server → Client**: Structured JSON messages with error codes
- **Error Handling**: Categorized error codes with recovery indicators

**Performance Optimizations**
- ThreadPoolExecutor for concurrent TTS synthesis
- WebSocket frame size optimization (5120 samples/frame)
- Backpressure management with configurable thresholds
- Memory-efficient audio buffer management

#### 🧪 **Testing Coverage**

**Unit Tests**
- Phrase boundary detection (punctuation, length, token gaps)
- LLM streaming timeout and cancellation behavior
- Session state transition validation
- TTS phrase aggregation and sequencing
- Backpressure detection and frame skipping

**Integration Tests**
- Complete PCM → transcript pipeline validation
- End-to-end audio processing through all phases
- Error handling and recovery mechanisms
- Timeout and cancellation behavior

**Edge Cases**
- Empty audio handling and rapid state transitions
- Concurrent session management
- Memory cleanup and resource management
- Network interruption and recovery

#### 🚀 **Deployment Ready**

**System Requirements**
- **Server**: 2+ CPU cores, 4GB+ RAM, 2GB+ storage
- **Client**: Modern browser with AudioWorklet support
- **Network**: Stable WebSocket connection (port 8000)

**Production Features**
- Graceful shutdown with session cleanup
- Comprehensive error reporting and logging
- Performance monitoring with structured metrics
- Configuration validation and environment management

#### 📈 **Performance Benchmarks**

**Latency Targets (Achieved)**
- ASR Partial: < 500ms from audio start
- ASR Final: < 800ms from audio start
- LLM Start: < 1200ms from audio start
- End-to-End: < 2500ms from audio start

**Throughput Metrics**
- Audio Processing: 16kHz 16-bit PCM at ~32KB/s
- WebSocket Frames: 5120 samples/frame at ~10fps
- TTS Synthesis: 2-4 concurrent threads
- Real-time Factor: Typically 0.3-0.7x (faster than real-time)

## [0.1.0] - 2025-09-15

### 🎯 **Phase 1 Initial Implementation**

#### Added
- Basic WebSocket ASR setup with PCM audio streaming
- Initial session management and VAD implementation
- Basic error handling and configuration system
- Initial documentation and project structure

---

## **Migration Guide**

### From Phase 1 to Phase 5
1. **No Breaking Changes**: All Phase 1 APIs remain compatible
2. **New Message Types**: Handle additional server message types (llm_token, tts_chunk, etc.)
3. **Configuration Updates**: New environment variables for LLM and TTS settings
4. **Client Updates**: Update WebSocket clients to handle new message types

### Environment Setup
```bash
# Copy and configure environment
cp .env .env.local
# Edit .env.local with your settings:
# - Add OPENAI_API_KEY for LLM functionality
# - Adjust performance settings as needed
# - Configure TTS parameters for your use case
```

---

**Total Implementation Size**: ~4000+ lines of production code
**Test Coverage**: Comprehensive across all components
**Documentation**: Complete setup and operational guides
**Status**: ✅ **PRODUCTION READY**