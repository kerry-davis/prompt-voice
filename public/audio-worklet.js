/**
 * AudioWorkletProcessor for real-time PCM streaming to WebSocket
 * Handles 16kHz resampling and Int16 LE binary format
 */

class PCMStreamingProcessor extends AudioWorkletProcessor {
  constructor() {
    super();

    // Target sample rate and frame configuration
    this.targetSampleRate = 16000;
    this.frameSize = 128; // Process in 128-sample chunks
    this.targetFrameSamples = 5120; // ~320ms at 16kHz

    // Resampling state
    this.resampleRatio = 1.0;
    this.resampleBuffer = new Float32Array(0);
    this.resampleBufferLength = 0;

    // Frame accumulation for WebSocket sending
    this.frameBuffer = new Int16Array(this.targetFrameSamples);
    this.frameBufferIndex = 0;

    // Performance tracking
    this.lastFrameTime = currentTime;

    // Latency tracking timestamps (Phase 4)
    this.timestamps = {
      t0_audio_start: null,  // First audio sample received
      t1_first_partial: null, // First partial transcript received
      t2_final_transcript: null, // Final transcript received
      t3_first_llm_token: null, // First LLM token received
      t4_first_tts_audio: null  // First TTS audio received
    };

    // Metrics tracking
    this.metrics = {
      audio_frames_sent: 0,
      total_samples_processed: 0,
      processing_start_time: null
    };

    this.port.onmessage = (event) => {
      const { type, data } = event.data;

      switch (type) {
        case 'START':
          this.isActive = true;
          this.frameBuffer = new Int16Array(this.targetFrameSamples);
          this.frameBufferIndex = 0;
          this.port.postMessage({ type: 'STARTED' });
          break;

        case 'STOP':
          this.isActive = false;
          this.flushRemainingFrame();
          this.port.postMessage({ type: 'STOPPED' });
          break;

        case 'CANCEL':
          this.isActive = false;
          this.frameBufferIndex = 0;
          this.port.postMessage({ type: 'CANCELLED' });
          break;
      }
    };
  }

  /**
   * Linear interpolation resampling
   */
  resample(inputBuffer, inputRate, outputRate) {
    const ratio = outputRate / inputRate;
    const outputLength = Math.floor(inputBuffer.length * ratio);
    const outputBuffer = new Float32Array(outputLength);

    for (let i = 0; i < outputLength; i++) {
      const inputIndex = i / ratio;
      const inputIndexInt = Math.floor(inputIndex);
      const fraction = inputIndex - inputIndexInt;

      if (inputIndexInt < inputBuffer.length - 1) {
        // Linear interpolation between samples
        outputBuffer[i] = inputBuffer[inputIndexInt] * (1 - fraction) +
                         inputBuffer[inputIndexInt + 1] * fraction;
      } else {
        // Use last sample for extrapolation
        outputBuffer[i] = inputBuffer[inputBuffer.length - 1];
      }
    }

    return outputBuffer;
  }

  /**
   * Convert Float32 audio data to Int16 PCM
   */
  float32ToInt16(float32Array) {
    const int16Array = new Int16Array(float32Array.length);

    for (let i = 0; i < float32Array.length; i++) {
      // Clamp to [-1, 1] and convert to Int16 range
      const clamped = Math.max(-1, Math.min(1, float32Array[i]));
      int16Array[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7FFF;
    }

    return int16Array;
  }

  /**
    * Send accumulated frame to main thread
    */
   sendFrame() {
     if (this.frameBufferIndex >= this.targetFrameSamples) {
       // Create a copy of the frame data
       const frameData = this.frameBuffer.slice(0, this.frameBufferIndex);

       // Update metrics (Phase 4)
       this.metrics.audio_frames_sent++;

       // Send to main thread for WebSocket transmission
       this.port.postMessage({
         type: 'AUDIO_FRAME',
         data: frameData.buffer,
         sampleCount: this.frameBufferIndex,
         timestamp: currentTime,
         // Include latency metrics (Phase 4)
         metrics: {
           audio_frames_sent: this.metrics.audio_frames_sent,
           total_samples_processed: this.metrics.total_samples_processed,
           processing_duration: this.metrics.processing_start_time ?
             currentTime - this.metrics.processing_start_time : 0
         }
       }, [frameData.buffer]);

       // Reset frame buffer
       this.frameBufferIndex = 0;
     }
   }

  /**
   * Flush any remaining samples in the frame buffer
   */
  flushRemainingFrame() {
    if (this.frameBufferIndex > 0) {
      // Pad with zeros if needed
      while (this.frameBufferIndex < this.targetFrameSamples) {
        this.frameBuffer[this.frameBufferIndex++] = 0;
      }

      this.sendFrame();
    }
  }

  /**
    * Main processing function called by AudioWorklet
    */
   process(inputs, outputs) {
     const input = inputs[0];
     if (!input || !input[0] || !this.isActive) {
       return true;
     }

     const inputData = input[0];
     const inputRate = sampleRate;

     // Track t0: first audio sample received (Phase 4)
     if (this.timestamps.t0_audio_start === null && inputData.length > 0) {
       this.timestamps.t0_audio_start = currentTime;
       this.metrics.processing_start_time = currentTime;
       console.log(`[Latency] t0_audio_start: ${this.timestamps.t0_audio_start}`);
     }

    // Resample if needed
    let processedData;
    if (inputRate !== this.targetSampleRate) {
      // Append to resample buffer
      const newBuffer = new Float32Array(this.resampleBufferLength + inputData.length);
      newBuffer.set(this.resampleBuffer, 0);
      newBuffer.set(inputData, this.resampleBufferLength);
      this.resampleBuffer = newBuffer;
      this.resampleBufferLength += inputData.length;

      // Calculate how many output samples we can produce
      const outputSamples = Math.floor(this.resampleBufferLength * this.targetSampleRate / inputRate);

      if (outputSamples > 0) {
        // Extract samples for resampling
        const resampleInput = this.resampleBuffer.slice(0, Math.ceil(outputSamples * inputRate / this.targetSampleRate));
        processedData = this.resample(resampleInput, inputRate, this.targetSampleRate);

        // Remove processed samples from buffer
        const remainingSamples = this.resampleBufferLength - Math.ceil(outputSamples * inputRate / this.targetSampleRate);
        if (remainingSamples > 0) {
          this.resampleBuffer = this.resampleBuffer.slice(-remainingSamples);
        } else {
          this.resampleBuffer = new Float32Array(0);
        }
        this.resampleBufferLength = remainingSamples;
      } else {
        processedData = new Float32Array(0);
      }
    } else {
      processedData = inputData;
    }

    // Convert to Int16 and accumulate in frame buffer
     if (processedData.length > 0) {
       const int16Data = this.float32ToInt16(processedData);

       // Update metrics (Phase 4)
       this.metrics.total_samples_processed += processedData.length;

       for (let i = 0; i < int16Data.length; i++) {
         if (this.frameBufferIndex < this.targetFrameSamples) {
           this.frameBuffer[this.frameBufferIndex++] = int16Data[i];
         } else {
           // Frame buffer full, send it
           this.sendFrame();
           this.frameBuffer[0] = int16Data[i];
           this.frameBufferIndex = 1;
         }
       }
     }

    // Copy input to output (pass-through)
    const output = outputs[0];
    if (output && output[0]) {
      output[0].set(inputData);
    }

    return true;
  }
}

// Register the processor
registerProcessor('pcm-streaming-processor', PCMStreamingProcessor);