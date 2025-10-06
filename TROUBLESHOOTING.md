# Troubleshooting Guide - Prompt Voice Streaming API

This guide covers common issues and solutions for the streaming voice system implementation.

## Table of Contents

1. [AudioWorklet Issues](#audioworklet-issues)
2. [WebSocket Connection Problems](#websocket-connection-problems)
3. [ASR/Model Performance Issues](#asrmodel-performance-issues)
4. [LLM Integration Problems](#llm-integration-problems)
5. [TTS Synthesis Issues](#tts-synthesis-issues)
6. [Latency and Performance](#latency-and-performance)
7. [Testing and Debugging](#testing-and-debugging)

## AudioWorklet Issues

### AudioWorklet Processor Not Loading

**Problem**: `AudioWorklet` fails to load with "addModule failed" error.

**Symptoms**:
- Browser console shows: `DOMException: The AudioWorkletNode could not be created`
- No PCM data being sent to server

**Solutions**:
1. **HTTPS Requirement**: AudioWorklet requires HTTPS in production
   ```javascript
   // Use HTTPS or localhost for development
   const ws = new WebSocket('wss://yourdomain.com/ws');
   // or
   const ws = new WebSocket('ws://localhost:8000/ws');
   ```

2. **CORS Configuration**: Ensure audio-worklet.js is served with correct MIME type
   ```apache
   # In .htaccess or nginx config
   AddType application/javascript .js
   ```

3. **Browser Compatibility**: Check browser support
   ```javascript
   if (!window.AudioWorkletNode) {
     console.error('AudioWorklet not supported in this browser');
     // Fallback to ScriptProcessorNode
   }
   ```

### Microphone Permission Denied

**Problem**: `getUserMedia` fails with permission denied.

**Symptoms**:
- Browser shows microphone permission prompt
- Console shows: `NotAllowedError: Permission denied`

**Solutions**:
1. **User Interaction**: Ensure microphone access is triggered by user gesture
   ```javascript
   // Must be called from click/touch event
   startButton.addEventListener('click', async () => {
     const stream = await navigator.mediaDevices.getUserMedia({
       audio: { echoCancellation: false, noiseSuppression: false }
     });
   });
   ```

2. **HTTPS Requirement**: Some browsers require HTTPS for microphone access

3. **Check Permissions API**:
   ```javascript
   const permissions = await navigator.permissions.query({ name: 'microphone' });
   if (permissions.state === 'denied') {
     // Guide user to browser settings
   }
   ```

### AudioContext Not Starting

**Problem**: AudioContext fails to start or gets suspended.

**Symptoms**:
- No audio processing occurs
- Console shows: `AudioContext state: suspended`

**Solutions**:
1. **Resume AudioContext**:
   ```javascript
   if (audioContext.state === 'suspended') {
     await audioContext.resume();
   }
   ```

2. **User Gesture Requirement**: AudioContext must be resumed from user interaction

3. **Check Sample Rate Compatibility**:
   ```javascript
   const context = new AudioContext({ sampleRate: 16000 });
   console.log('Actual sample rate:', context.sampleRate);
   ```

## WebSocket Connection Problems

### Connection Timeout

**Problem**: WebSocket connection times out.

**Symptoms**:
- Connection fails after 10 seconds
- No server messages received

**Solutions**:
1. **Server Running**: Ensure Python server is running
   ```bash
   python app.py
   # or
   uvicorn app:app --host 0.0.0.0 --port 8000 --reload
   ```

2. **Firewall Configuration**: Check firewall settings
   ```bash
   # Check if port 8000 is open
   netstat -tlnp | grep 8000
   # or
   ufw allow 8000
   ```

3. **Network Configuration**: Verify WebSocket URL
   ```javascript
   // Use correct protocol and port
   const wsUrl = `ws://${window.location.hostname}:8000/ws`;
   ```

### Binary Frame Size Issues

**Problem**: Server rejects binary frames as too large.

**Symptoms**:
- Error: `Binary frame too large: X bytes (max: 65536)`
- No audio processing occurs

**Solutions**:
1. **Frame Size Configuration**: Adjust PCM chunk size in settings
   ```python
   # In app.py StreamingSettings
   stream_pcm_chunk_size = 5120  # Reduce if needed
   ```

2. **Client Frame Size**: Ensure client sends appropriate frame sizes
   ```javascript
   // In audio-worklet.js
   const frameSize = 5120; // Match server expectation
   ```

### WebSocket Queue Backpressure

**Problem**: WebSocket queue becomes full, causing frame skipping.

**Symptoms**:
- Info message: `Throttling partial transcripts due to backpressure`
- Reduced transcript frequency

**Solutions**:
1. **Increase Queue Threshold**:
   ```python
   # In .env
   STREAM_BACKPRESSURE_THRESHOLD=100  # Increase from default 50
   ```

2. **Reduce Frame Skipping**:
   ```python
   # In .env
   STREAM_BACKPRESSURE_SKIP_FRAMES=2  # Reduce from default 3
   ```

3. **Optimize Processing**: Check for bottlenecks in ASR/LLM/TTS pipeline

## ASR/Model Performance Issues

### Whisper Model Loading Fails

**Problem**: Whisper model fails to load or causes memory issues.

**Symptoms**:
- Error: `Failed to load Whisper model: ...`
- High memory usage
- Slow startup time

**Solutions**:
1. **Model Size Selection**: Use smaller model for testing
   ```python
   # In .env
   STREAM_MODEL_WHISPER=tiny-int8  # Instead of small-int8
   ```

2. **Memory Management**: Ensure sufficient RAM (4GB+ recommended)

3. **Model Path Issues**: Check model download location
   ```bash
   # Check if model exists
   python -c "from faster_whisper import WhisperModel; print('Model loaded successfully')"
   ```

### Poor Transcription Quality

**Problem**: ASR produces inaccurate or incomplete transcripts.

**Symptoms**:
- Incorrect words in transcripts
- Missing words or phrases
- Poor handling of accents/noise

**Solutions**:
1. **Audio Quality**: Ensure clean audio input
   ```javascript
   // Request high-quality audio constraints
   const constraints = {
     audio: {
       echoCancellation: false,
       noiseSuppression: false,
       autoGainControl: false,
       sampleRate: 16000,
       channelCount: 1
     }
   };
   ```

2. **VAD Sensitivity**: Adjust voice activity detection
   ```python
   # In .env - reduce for more sensitive detection
   STREAM_VAD_SILENCE_MS=500  # Reduce from 700ms
   ```

3. **Model Parameters**: Fine-tune Whisper settings
   ```python
   # In app.py transcription call
   segments, info = model.transcribe(
     audio_array,
     language="en",
     vad_filter=True,
     vad_parameters=dict(threshold=0.5)
   )
   ```

### High ASR Latency

**Problem**: Significant delay between speech and transcript.

**Symptoms**:
- Transcripts appear seconds after speech
- Poor real-time experience

**Solutions**:
1. **Buffer Size**: Reduce audio buffer size for faster processing
   ```python
   # In .env
   STREAM_PCM_CHUNK_SIZE=2560  # Reduce from 5120
   ```

2. **Partial Interval**: Increase partial transcript frequency
   ```python
   # In .env
   STREAM_PARTIAL_INTERVAL_MS=500  # Reduce from 1000ms
   ```

3. **Model Optimization**: Use faster Whisper settings
   ```python
   # In app.py model initialization
   model = WhisperModel("tiny-int8", device="cpu", compute_type="int8")
   ```

## LLM Integration Problems

### OpenAI API Key Issues

**Problem**: OpenAI API requests fail due to authentication.

**Symptoms**:
- Error: `Invalid API key` or `Unauthorized`
- LLM fallback mode activated

**Solutions**:
1. **API Key Configuration**:
   ```bash
   # In .env
   OPENAI_API_KEY=sk-your-actual-api-key-here
   ```

2. **Key Permissions**: Ensure API key has correct permissions

3. **Network Access**: Verify internet connectivity for API calls

### LLM Timeout Issues

**Problem**: LLM streaming times out during response generation.

**Symptoms**:
- Error: `LLM_TIMEOUT` after 30 seconds
- Incomplete responses

**Solutions**:
1. **Increase Timeout**:
   ```python
   # In .env
   STREAM_LLM_TIMEOUT_S=60  # Increase from 30s
   ```

2. **Token Timeout**: Check for 5-second token gaps
   ```python
   # In .env - increase token timeout
   # This is hardcoded to 5 seconds in current implementation
   ```

3. **Model Selection**: Use faster model
   ```python
   # In .env
   OPENAI_MODEL=gpt-3.5-turbo  # Instead of gpt-4
   ```

### Local Fallback Issues

**Problem**: Local LLM fallback produces poor responses.

**Symptoms**:
- Generic responses not related to input
- Poor conversation flow

**Solutions**:
1. **Fallback Response Quality**: Current implementation uses simple template
   ```python
   # In app.py _stream_local_fallback method
   # This is a simple template - enhance as needed
   fallback_response = f"Thank you for saying: '{user_input}'..."
   ```

2. **Enable OpenAI**: Use actual LLM for better quality
   ```bash
   # Set OPENAI_API_KEY in .env
   OPENAI_API_KEY=sk-your-key
   ```

## TTS Synthesis Issues

### pyttsx3 Engine Initialization Fails

**Problem**: TTS engine fails to initialize.

**Symptoms**:
- Error: `Failed to initialize pyttsx3 engine`
- No TTS audio generated

**Solutions**:
1. **System Dependencies**: Install required system packages
   ```bash
   # Ubuntu/Debian
   sudo apt-get install espeak espeak-ng

   # macOS
   brew install espeak

   # Windows - should work out of box
   ```

2. **Engine Selection**: Verify pyttsx3 installation
   ```bash
   python -c "import pyttsx3; engine = pyttsx3.init(); print('TTS OK')"
   ```

3. **Voice Selection**: Check available voices
   ```python
   # In app.py Pyttsx3Engine
   voices = engine.getProperty('voices')
   if voices:
     engine.setProperty('voice', voices[0].id)
   ```

### TTS Audio Quality Issues

**Problem**: TTS audio is poor quality or distorted.

**Symptoms**:
- Robotic or unclear speech
- Incorrect pronunciation
- Audio artifacts

**Solutions**:
1. **Voice Settings**: Adjust pyttsx3 voice parameters
   ```python
   # In app.py Pyttsx3Engine
   engine.setProperty('rate', 200)    # Words per minute
   engine.setProperty('volume', 0.9)  # 0.0 to 1.0
   ```

2. **Engine Alternatives**: Consider other TTS engines
   ```python
   # In .env
   STREAM_TTS_ENGINE=pyttsx3  # Currently only option
   # Future: could add coqui-tts, tortoise-tts, etc.
   ```

### TTS Queue Backlog

**Problem**: TTS phrases queue up and playback lags.

**Symptoms**:
- Multiple phrases pending synthesis
- Delayed audio playback

**Solutions**:
1. **Increase Thread Workers**:
   ```python
   # In .env
   STREAM_TTS_THREAD_WORKERS=4  # Increase from 2
   ```

2. **Reduce Phrase Length**:
   ```python
   # In .env
   STREAM_TTS_MAX_PHRASE_LENGTH=40  # Reduce from 60
   ```

3. **Queue Limits**: Monitor and adjust queue size
   ```python
   # In .env
   STREAM_MAX_PENDING_PHRASES=10  # Increase from 5
   ```

## Latency and Performance

### High End-to-End Latency

**Problem**: Significant delay from speech to TTS response.

**Symptoms**:
- Total latency > 2 seconds
- Poor conversational experience

**Solutions**:
1. **Monitor Latency Metrics**: Check server logs for timing
   ```
   [Latency] Δ t1-t0 (ASR latency): 500ms
   [Latency] Δ t2-t0 (Final ASR latency): 800ms
   [Latency] Δ t3-t0 (LLM start latency): 1200ms
   [Latency] Δ t4-t0 (End-to-end latency): 2500ms
   ```

2. **Optimize Each Stage**:
   - ASR: Use smaller model, reduce buffer size
   - LLM: Use faster model, reduce max_tokens
   - TTS: Increase thread workers, reduce phrase length

3. **Network Optimization**: Minimize WebSocket round trips

### Real-Time Factor (RTF) Issues

**Problem**: Audio processing is slower than real-time.

**Symptoms**:
- RTF > 1.0 (processing slower than real-time)
- Audio buffer overflows

**Solutions**:
1. **Hardware Acceleration**: Use GPU for Whisper if available
   ```python
   # In app.py model initialization
   model = WhisperModel("small-int8", device="cuda", compute_type="float16")
   ```

2. **Processing Optimization**: Reduce processing load
   ```python
   # Reduce VAD sensitivity
   STREAM_VAD_SILENCE_MS=1000  # Increase from 700ms

   # Reduce partial transcript frequency
   STREAM_PARTIAL_INTERVAL_MS=2000  # Increase from 1000ms
   ```

3. **Resource Monitoring**: Check CPU/memory usage
   ```bash
   # Monitor system resources
   htop  # or top
   ```

## Testing and Debugging

### Running Tests

**Problem**: Tests fail or don't run properly.

**Symptoms**:
- Import errors
- Missing dependencies
- Test timeouts

**Solutions**:
1. **Install Test Dependencies**:
   ```bash
   pip install pytest pytest-asyncio
   ```

2. **Run Specific Tests**:
   ```bash
   # Run comprehensive streaming tests
   python test_streaming_comprehensive.py

   # Run Phase 2 validation tests
   python test_phase2_implementation.py
   ```

3. **Debug Mode**: Run with verbose output
   ```bash
   python -m pytest test_streaming_comprehensive.py -v -s
   ```

### Log Analysis

**Problem**: Understanding system behavior through logs.

**Symptoms**:
- Unclear error causes
- Performance issues not visible

**Solutions**:
1. **Enable Debug Logging**:
   ```python
   # In app.py
   logging.basicConfig(level=logging.DEBUG)
   ```

2. **Structured Metrics**: Look for JSON-formatted metrics
   ```
   [LATENCY_METRICS] {"event": "latency", "session_id": 123, ...}
   [Metrics] Session 123 - Audio frames: 100, Samples: 51200
   ```

3. **Client-Side Debugging**: Check browser console for timing data
   ```
   [CLIENT_LATENCY_METRICS] {"event": "latency_client", ...}
   ```

### Performance Profiling

**Problem**: Identifying performance bottlenecks.

**Symptoms**:
- Slow processing
- High memory usage
- CPU spikes

**Solutions**:
1. **Python Profiling**:
   ```bash
   python -m cProfile -s time app.py
   ```

2. **Memory Profiling**:
   ```bash
   pip install memory_profiler
   python -m memory_profiler app.py
   ```

3. **Browser DevTools**: Use Performance tab for client-side analysis

## Getting Help

If issues persist:

1. **Check Logs**: Review server and browser console logs
2. **Test Components**: Isolate issues by testing individual components
3. **Version Compatibility**: Ensure all dependencies are compatible
4. **Resource Limits**: Check system resource constraints

For additional support, check the [README.md](README.md) for configuration options and the [test files](test_streaming_comprehensive.py) for expected behavior.