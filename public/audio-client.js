/**
 * Real-time Audio Streaming Client
 * Integrates AudioWorklet with WebSocket for PCM streaming
 */

class AudioStreamingClient {
  constructor(config = {}) {
    // Configuration
    this.serverUrl = config.serverUrl || 'ws://localhost:8000/ws';
    this.targetSampleRate = 16000;
    this.frameSize = 5120; // ~320ms at 16kHz

    // State management
    this.isConnected = false;
    this.isRecording = false;
    this.websocket = null;
    this.audioContext = null;
    this.workletNode = null;
    this.microphone = null;

    // Connection management
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 1000; // Start with 1 second
    this.maxReconnectDelay = 30000; // Max 30 seconds

    // Performance tracking
     this.startTime = null;
     this.frameCount = 0;
     this.totalBytesSent = 0;

     // Latency tracking timestamps (Phase 4)
     this.timestamps = {
       t0_audio_start: null,  // First audio sample received
       t1_first_partial: null, // First partial transcript received
       t2_final_transcript: null, // Final transcript received
       t3_first_llm_token: null, // First LLM token received
       t4_first_tts_audio: null  // First TTS audio received
     };

     // Metrics tracking (Phase 4)
      this.metrics = {
        audio_frames_sent: 0,
        total_samples_processed: 0,
        total_processing_time: 0,
        transcripts_received: 0,
        llm_tokens_received: 0,
        tts_chunks_received: 0
      };

      // TTS Playback Queue (Phase 3)
      this.ttsQueue = [];
      this.isPlayingTTS = false;
      this.currentAudioContext = null;
      this.playbackGainNode = null;

    // Event handlers
    this.onConnected = config.onConnected || (() => {});
    this.onDisconnected = config.onDisconnected || (() => {});
    this.onError = config.onError || ((error) => console.error('AudioStreamingClient error:', error));
    this.onPartialTranscript = config.onPartialTranscript || (() => {});
    this.onFinalTranscript = config.onFinalTranscript || (() => {});
    this.onInfo = config.onInfo || (() => {});

    this.init();
  }

  async init() {
    try {
      // Initialize audio context
      await this.initAudioContext();

      // Load AudioWorklet processor
      await this.loadAudioWorklet();

      console.log('AudioStreamingClient initialized successfully');
    } catch (error) {
      this.onError(`Initialization failed: ${error.message}`);
    }
  }

  async initAudioContext() {
    try {
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: this.targetSampleRate
      });

      // Resume context if suspended (required by some browsers)
      if (this.audioContext.state === 'suspended') {
        await this.audioContext.resume();
      }

      console.log(`AudioContext initialized with sample rate: ${this.audioContext.sampleRate}Hz`);
    } catch (error) {
      throw new Error(`Failed to initialize AudioContext: ${error.message}`);
    }
  }

  async loadAudioWorklet() {
    try {
      // Load the AudioWorklet processor
  await this.audioContext.audioWorklet.addModule('/static/audio-worklet.js');

      console.log('AudioWorklet processor loaded successfully');
    } catch (error) {
      throw new Error(`Failed to load AudioWorklet: ${error.message}`);
    }
  }

  async connect() {
    try {
      if (this.isConnected) {
        console.warn('Already connected to server');
        return;
      }

      console.log(`Connecting to WebSocket: ${this.serverUrl}`);

      this.websocket = new WebSocket(this.serverUrl);

      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          reject(new Error('Connection timeout'));
        }, 10000);

        this.websocket.onopen = () => {
          clearTimeout(timeout);
          this.isConnected = true;
          this.reconnectAttempts = 0;
          this.reconnectDelay = 1000;

          console.log('WebSocket connected successfully');
          this.onConnected();

          resolve();
        };

        this.websocket.onmessage = (event) => {
          this.handleServerMessage(event);
        };

        this.websocket.onclose = (event) => {
          clearTimeout(timeout);
          this.isConnected = false;

          console.log(`WebSocket disconnected: ${event.code} ${event.reason}`);

          if (event.code !== 1000) { // Not a normal closure
            this.scheduleReconnect();
          }

          this.onDisconnected(event);
        };

        this.websocket.onerror = (error) => {
          clearTimeout(timeout);
          console.error('WebSocket error:', error);
          this.onError(`WebSocket error: ${error.message || 'Unknown error'}`);
          reject(error);
        };
      });
    } catch (error) {
      this.onError(`Connection failed: ${error.message}`);
      throw error;
    }
  }

  scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.onError('Max reconnection attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1), this.maxReconnectDelay);

    console.log(`Scheduling reconnection attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts} in ${delay}ms`);

    setTimeout(() => {
      this.connect().catch(error => {
        console.error(`Reconnection attempt ${this.reconnectAttempts} failed:`, error);
      });
    }, delay);
  }

  handleServerMessage(event) {
     try {
       let message;

       if (typeof event.data === 'string') {
         message = JSON.parse(event.data);
       } else {
         // Binary data (shouldn't happen for server messages in this implementation)
         console.warn('Received unexpected binary message from server');
         return;
       }

       const now = performance.now();

       switch (message.type) {
         case 'partial_transcript':
           // Track t1: first partial transcript received (Phase 4)
           if (this.timestamps.t1_first_partial === null) {
             this.timestamps.t1_first_partial = now;
             console.log(`[Latency] t1_first_partial: ${this.timestamps.t1_first_partial.toFixed(2)}ms`);
             this.logLatencyDeltas(); // Log current latency state
           }
           this.metrics.transcripts_received++;
           this.onPartialTranscript(message.text);
           break;

         case 'final_transcript':
           // Track t2: final transcript received (Phase 4)
           if (this.timestamps.t2_final_transcript === null) {
             this.timestamps.t2_final_transcript = now;
             console.log(`[Latency] t2_final_transcript: ${this.timestamps.t2_final_transcript.toFixed(2)}ms`);
             this.logLatencyDeltas(); // Log current latency state
           }
           this.metrics.transcripts_received++;
           this.onFinalTranscript(message.text, message.id);
           break;

         case 'llm_token':
           // Track t3: first LLM token received (Phase 4)
           if (this.timestamps.t3_first_llm_token === null && message.text) {
             this.timestamps.t3_first_llm_token = now;
             console.log(`[Latency] t3_first_llm_token: ${this.timestamps.t3_first_llm_token.toFixed(2)}ms`);
             this.logLatencyDeltas(); // Log current latency state
           }
           if (message.text) {
             this.metrics.llm_tokens_received++;
           }
           break;

         case 'tts_chunk':
           // Track t4: first TTS audio received (Phase 4)
           if (this.timestamps.t4_first_tts_audio === null) {
             this.timestamps.t4_first_tts_audio = now;
             console.log(`[Latency] t4_first_tts_audio: ${this.timestamps.t4_first_tts_audio.toFixed(2)}ms`);
             this.logLatencyDeltas(); // Log current latency state

             // Log structured latency metrics for correlation (Phase 4)
             this.logStructuredLatencyMetrics();
           }
           this.metrics.tts_chunks_received++;

           // Add to TTS playback queue (Phase 3)
           this.enqueueTTSChunk(message.seq, message.audio_b64, message.mime);
           break;

         case 'tts_phrase_done':
           // Mark phrase as complete in queue (Phase 3)
           this.markTTSPhraseComplete(message.seq);
           break;

         case 'error':
           this.onError(message.message);
           break;

         case 'info':
           console.log('Server info:', message.message);
           this.onInfo(message.message);
           break;

         default:
           console.warn('Unknown message type from server:', message.type);
       }
     } catch (error) {
       console.error('Error handling server message:', error);
     }
   }

  async startRecording() {
    try {
      if (!this.isConnected) {
        throw new Error('Not connected to server');
      }

      if (this.isRecording) {
        console.warn('Already recording');
        return;
      }

      // Request microphone permission
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          sampleRate: this.targetSampleRate
        }
      });

      this.microphone = stream.getAudioTracks()[0];

      // Create AudioWorkletNode
      this.workletNode = new AudioWorkletNode(this.audioContext, 'pcm-streaming-processor');

      // Set up message handling from AudioWorklet
      this.workletNode.port.onmessage = (event) => {
        this.handleAudioFrame(event);
      };

      // Connect microphone to worklet
      const source = this.audioContext.createMediaStreamSource(stream);
      source.connect(this.workletNode);
      this.workletNode.connect(this.audioContext.destination);

      // Start the worklet processor
      this.workletNode.port.postMessage({ type: 'START' });

      // Send start message to server
      this.websocket.send(JSON.stringify({
        type: 'start',
        sample_rate: this.targetSampleRate
      }));

      this.isRecording = true;
      this.startTime = Date.now();
      this.frameCount = 0;
      this.totalBytesSent = 0;

      console.log('Recording started');

    } catch (error) {
      this.onError(`Failed to start recording: ${error.message}`);
      throw error;
    }
  }

  handleAudioFrame(event) {
     const { type, data, sampleCount, timestamp, metrics } = event.data;

     if (type === 'AUDIO_FRAME' && this.isRecording && this.isConnected) {
       // Track t0: first audio frame received from AudioWorklet (Phase 4)
       if (this.timestamps.t0_audio_start === null) {
         this.timestamps.t0_audio_start = performance.now();
         console.log(`[Latency] t0_audio_start: ${this.timestamps.t0_audio_start.toFixed(2)}ms`);
       }

       // Update metrics from AudioWorklet (Phase 4)
       if (metrics) {
         this.metrics.audio_frames_sent = metrics.audio_frames_sent;
         this.metrics.total_samples_processed = metrics.total_samples_processed;
         this.metrics.total_processing_time = metrics.processing_duration;
       }

       // Send binary frame to server
       this.websocket.send(data);

       this.frameCount++;
       this.totalBytesSent += data.byteLength;

       // Log performance periodically (Phase 4 enhanced)
       if (this.frameCount % 10 === 0) {
         const elapsed = (Date.now() - this.startTime) / 1000;
         const avgFrameRate = this.frameCount / elapsed;
         const avgBytesPerSecond = this.totalBytesSent / elapsed;

         console.log(`[Metrics] Audio frames: ${this.frameCount}, Rate: ${avgFrameRate.toFixed(1)} fps, Bandwidth: ${avgBytesPerSecond.toFixed(0)} B/s`);
         console.log(`[Metrics] Samples processed: ${this.metrics.total_samples_processed}, Processing time: ${this.metrics.total_processing_time.toFixed(3)}s`);
       }
     }
   }

  stopRecording() {
    try {
      if (!this.isRecording) {
        console.warn('Not currently recording');
        return;
      }

      // Stop the worklet processor
      if (this.workletNode) {
        this.workletNode.port.postMessage({ type: 'STOP' });
      }

      // Send stop message to server
      if (this.isConnected) {
        this.websocket.send(JSON.stringify({ type: 'stop' }));
      }

      // Clean up audio resources
      this.cleanupAudio();

      this.isRecording = false;

      console.log('Recording stopped');

    } catch (error) {
      this.onError(`Failed to stop recording: ${error.message}`);
    }
  }

  cancelRecording() {
    try {
      if (!this.isRecording && !this.isConnected) {
        console.warn('Not currently recording or connected');
        return;
      }

      // Cancel the worklet processor
      if (this.workletNode) {
        this.workletNode.port.postMessage({ type: 'CANCEL' });
      }

      // Send cancel message to server
      if (this.isConnected) {
        this.websocket.send(JSON.stringify({ type: 'cancel' }));
      }

      // Clean up audio resources
      this.cleanupAudio();

      this.isRecording = false;

      console.log('Recording cancelled');

    } catch (error) {
      this.onError(`Failed to cancel recording: ${error.message}`);
    }
  }

  cleanupAudio() {
    if (this.microphone) {
      this.microphone.stop();
      this.microphone = null;
    }

    if (this.workletNode) {
      this.workletNode.disconnect();
      this.workletNode = null;
    }
  }

  disconnect() {
    try {
      if (this.isRecording) {
        this.stopRecording();
      }

      if (this.websocket) {
        this.websocket.close(1000, 'Client disconnect');
        this.websocket = null;
      }

      this.isConnected = false;

      console.log('Disconnected from server');

    } catch (error) {
      this.onError(`Failed to disconnect: ${error.message}`);
    }
  }

  // Latency logging and metrics (Phase 4)
   logLatencyDeltas() {
     const t = this.timestamps;
     const deltas = {};

     if (t.t0_audio_start !== null) {
       if (t.t1_first_partial !== null) {
         deltas.t1_t0 = t.t1_first_partial - t.t0_audio_start;
         console.log(`[Latency] Δ t1-t0 (ASR latency): ${deltas.t1_t0.toFixed(2)}ms`);
       }
       if (t.t2_final_transcript !== null) {
         deltas.t2_t0 = t.t2_final_transcript - t.t0_audio_start;
         console.log(`[Latency] Δ t2-t0 (Final ASR latency): ${deltas.t2_t0.toFixed(2)}ms`);
       }
       if (t.t3_first_llm_token !== null) {
         deltas.t3_t0 = t.t3_first_llm_token - t.t0_audio_start;
         console.log(`[Latency] Δ t3-t0 (LLM start latency): ${deltas.t3_t0.toFixed(2)}ms`);
       }
       if (t.t4_first_tts_audio !== null) {
         deltas.t4_t0 = t.t4_first_tts_audio - t.t0_audio_start;
         console.log(`[Latency] Δ t4-t0 (End-to-end latency): ${deltas.t4_t0.toFixed(2)}ms`);
       }
     }

     // Calculate RTF (Real-time factor) for audio processing (Phase 4)
     if (this.metrics.total_samples_processed > 0 && this.metrics.total_processing_time > 0) {
       const audio_duration = this.metrics.total_samples_processed / this.targetSampleRate;
       const rtf = audio_duration / this.metrics.total_processing_time;
       console.log(`[RTF] Real-time factor: ${rtf.toFixed(3)}x (${rtf < 1 ? 'faster' : 'slower'} than real-time)`);
     }
   }

   // Structured latency metrics logging for correlation (Phase 4)
   logStructuredLatencyMetrics() {
     const t = this.timestamps;

     // Check if we have all required timestamps
     if (!t.t0_audio_start || !t.t1_first_partial || !t.t2_final_transcript ||
         !t.t3_first_llm_token || !t.t4_first_tts_audio) {
       return;
     }

     // Calculate latency metrics in milliseconds
     const t_first_partial_ms = t.t1_first_partial - t.t0_audio_start;
     const t_final_transcript_ms = t.t2_final_transcript - t.t0_audio_start;
     const t_first_token_ms = t.t3_first_llm_token - t.t0_audio_start;
     const t_first_audio_ms = t.t4_first_tts_audio - t.t0_audio_start;
     const total_reply_ms = t.t4_first_tts_audio - t.t0_audio_start;

     // Create structured latency log for client-side correlation
     const latencyData = {
       event: "latency_client",
       t_first_partial_ms: Math.round(t_first_partial_ms),
       t_final_transcript_ms: Math.round(t_final_transcript_ms),
       t_first_token_ms: Math.round(t_first_token_ms),
       t_first_audio_ms: Math.round(t_first_audio_ms),
       total_reply_ms: Math.round(total_reply_ms),
       timestamp: Date.now()
     };

     // Log as structured JSON for correlation with server logs
     console.log(`[CLIENT_LATENCY_METRICS] ${JSON.stringify(latencyData)}`);

     // Also log individual deltas for detailed analysis
     console.log(`[Client Latency] Partial: ${t_first_partial_ms.toFixed(2)}ms, ` +
                `Final: ${t_final_transcript_ms.toFixed(2)}ms, ` +
                `Token: ${t_first_token_ms.toFixed(2)}ms, ` +
                `Audio: ${t_first_audio_ms.toFixed(2)}ms, ` +
                `Total: ${total_reply_ms.toFixed(2)}ms`);
   }

  // TTS Playback Queue Methods (Phase 3)
  async initTTSPlayback() {
    try {
      if (!this.currentAudioContext) {
        this.currentAudioContext = new (window.AudioContext || window.webkitAudioContext)();

        // Resume context if suspended
        if (this.currentAudioContext.state === 'suspended') {
          await this.currentAudioContext.resume();
        }

        // Create gain node for volume control
        this.playbackGainNode = this.currentAudioContext.createGain();
        this.playbackGainNode.gain.value = 0.8; // Default volume
        this.playbackGainNode.connect(this.currentAudioContext.destination);

        console.log('TTS playback context initialized');
      }
    } catch (error) {
      console.error('Failed to initialize TTS playback:', error);
    }
  }

  enqueueTTSChunk(sequence, audioBase64, mimeType) {
    // Initialize TTS playback if not already done
    if (!this.currentAudioContext) {
      this.initTTSPlayback();
    }

    // Add chunk to queue
    this.ttsQueue.push({
      sequence: sequence,
      audioBase64: audioBase64,
      mimeType: mimeType || 'audio/wav',
      enqueuedAt: Date.now()
    });

    console.log(`TTS chunk ${sequence} enqueued (queue size: ${this.ttsQueue.length})`);

    // Start playback if not already playing
    if (!this.isPlayingTTS && this.ttsQueue.length > 0) {
      this.processTTSQueue();
    }
  }

  markTTSPhraseComplete(sequence) {
    // Find the chunk in queue and mark it as complete
    const chunk = this.ttsQueue.find(item => item.sequence === sequence);
    if (chunk) {
      chunk.isComplete = true;
      console.log(`TTS phrase ${sequence} marked as complete`);
    }
  }

  async processTTSQueue() {
    if (this.isPlayingTTS || this.ttsQueue.length === 0) {
      return;
    }

    this.isPlayingTTS = true;

    try {
      while (this.ttsQueue.length > 0) {
        // Find the next chunk to play (lowest sequence number)
        const nextChunk = this.ttsQueue
          .filter(chunk => chunk.isComplete)
          .sort((a, b) => a.sequence - b.sequence)[0];

        if (!nextChunk) {
          // No complete chunks available, wait a bit
          await new Promise(resolve => setTimeout(resolve, 50));
          continue;
        }

        // Remove from queue
        const index = this.ttsQueue.indexOf(nextChunk);
        this.ttsQueue.splice(index, 1);

        // Play the audio chunk
        await this.playTTSAudio(nextChunk);

        console.log(`Played TTS chunk ${nextChunk.sequence}, queue size: ${this.ttsQueue.length}`);
      }
    } catch (error) {
      console.error('Error processing TTS queue:', error);
    } finally {
      this.isPlayingTTS = false;
    }
  }

  async playTTSAudio(chunk) {
    return new Promise((resolve, reject) => {
      try {
        // Decode base64 audio data
        const binaryString = atob(chunk.audioBase64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }

        // Create audio buffer
        this.currentAudioContext.decodeAudioData(bytes.buffer, (buffer) => {
          // Create and play audio source
          const source = this.currentAudioContext.createBufferSource();
          source.buffer = buffer;
          source.connect(this.playbackGainNode);

          source.onended = () => {
            resolve();
          };

          source.onerror = (error) => {
            console.error('Error playing TTS audio:', error);
            reject(error);
          };

          source.start(0);
        }, (error) => {
          console.error('Error decoding TTS audio data:', error);
          reject(error);
        });
      } catch (error) {
        console.error('Error in playTTSAudio:', error);
        reject(error);
      }
    });
  }

  clearTTSQueue() {
    this.ttsQueue = [];
    console.log('TTS queue cleared');
  }

  getTTSQueueStatus() {
    return {
      queueSize: this.ttsQueue.length,
      isPlaying: this.isPlayingTTS,
      nextSequence: this.ttsQueue.length > 0 ? Math.min(...this.ttsQueue.map(item => item.sequence)) : null,
      pendingSequences: this.ttsQueue.map(item => item.sequence).sort((a, b) => a - b)
    };
  }

  setTTSVolume(volume) {
    if (this.playbackGainNode) {
      this.playbackGainNode.gain.value = Math.max(0, Math.min(1, volume));
      console.log(`TTS volume set to ${volume}`);
    }
  }

  // Getters for status information
    getStatus() {
     return {
       isConnected: this.isConnected,
       isRecording: this.isRecording,
       reconnectAttempts: this.reconnectAttempts,
       frameCount: this.frameCount,
       totalBytesSent: this.totalBytesSent,
       audioContextState: this.audioContext ? this.audioContext.state : 'none',
       sampleRate: this.audioContext ? this.audioContext.sampleRate : 0,
       // Include latency metrics (Phase 4)
       timestamps: this.timestamps,
       metrics: this.metrics,
       // Include TTS queue status (Phase 3)
       ttsQueueSize: this.ttsQueue.length,
       isPlayingTTS: this.isPlayingTTS
     };
   }
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
  module.exports = AudioStreamingClient;
} else {
  window.AudioStreamingClient = AudioStreamingClient;
}