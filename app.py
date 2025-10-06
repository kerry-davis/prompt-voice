"""
Prompt Voice Streaming API - v2 Step 1 Implementation
Core WebSocket + session management + config parsing + start/stop/cancel handling
"""

import asyncio
import logging
import os
import time
import json
import signal
import sys
import tempfile
import base64
import threading
from collections import deque
from contextlib import asynccontextmanager
from typing import Dict, Optional, List
from enum import Enum
from dataclasses import dataclass, field
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# ASR imports (optional for smoke tests)
try:  # pragma: no cover - optional dependency environment
    from faster_whisper import WhisperModel  # type: ignore
    import numpy as np  # type: ignore
except Exception as e:  # Do not raise; streaming will gracefully degrade
    WhisperModel = None  # type: ignore
    np = None  # type: ignore
    logger.warning(f"ASR optional dependencies unavailable: {e}")

from backend.stream.config import load_stream_config
from backend.stream.websocket import handle as stream_ws_handle


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StreamingSettings(BaseSettings):
    """Configuration settings for v2 streaming functionality"""

    # VAD settings
    stream_vad_silence_ms: int = 700
    stream_vad_frame_ms: int = 30
    stream_vad_sample_rate: int = 16000

    # ASR settings
    stream_partial_interval_ms: int = 1000
    stream_max_utterance_ms: int = 30000
    stream_model_whisper: str = "small-int8"  # Default model for faster-whisper
    stream_asr_timeout_s: float = 10.0  # ASR transcription timeout
    stream_pcm_chunk_size: int = 5120

    # Backpressure settings (Phase 4)
    stream_backpressure_threshold: int = 50
    stream_backpressure_skip_frames: int = 3

    # LLM settings (Phase 2)
    openai_api_key: str = ""
    openai_model: str = "gpt-3.5-turbo"
    openai_max_tokens: int = 150
    openai_temperature: float = 0.7
    stream_llm_timeout_s: float = 30.0
    llm_use_local_fallback: bool = False

    # TTS settings (Phase 3)
    stream_tts_engine: str = "pyttsx3"
    stream_tts_max_phrase_length: int = 60
    stream_tts_thread_workers: int = 2
    stream_tts_timeout_s: float = 10.0
    stream_max_pending_phrases: int = 5

    # Logging settings (Phase 4)
    stream_log_latency: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = False
        # Ignore unexpected extra keys (e.g. host, port, debug passed by wrappers)
        extra = "ignore"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Validate configuration values
        self._validate_config()

    def _validate_config(self):
        """Validate configuration values and raise errors for invalid settings"""
        errors = []

        # Validate VAD silence setting (300-2000ms)
        if self.stream_vad_silence_ms < 300 or self.stream_vad_silence_ms > 2000:
            errors.append(f"stream_vad_silence_ms must be between 300 and 2000, got {self.stream_vad_silence_ms}")

        # Validate partial interval (250-3000ms)
        if self.stream_partial_interval_ms < 250 or self.stream_partial_interval_ms > 3000:
            errors.append(f"stream_partial_interval_ms must be between 250 and 3000, got {self.stream_partial_interval_ms}")

        # Validate max utterance (<=120000ms)
        if self.stream_max_utterance_ms > 120000:
            errors.append(f"stream_max_utterance_ms must be <= 120000, got {self.stream_max_utterance_ms}")

        # Validate ASR timeout (1-30 seconds)
        if self.stream_asr_timeout_s < 1.0 or self.stream_asr_timeout_s > 30.0:
            errors.append(f"stream_asr_timeout_s must be between 1.0 and 30.0, got {self.stream_asr_timeout_s}")

        # Raise error if validation failed
        if errors:
            error_msg = f"Configuration validation failed:\n" + "\n".join(f"  - {error}" for error in errors)
            logger.error(error_msg)
            raise ValueError(error_msg)


# SessionState data structure exactly as specified in section 3
class SessionPhase(Enum):
    """Session phase enumeration"""
    IDLE = "IDLE"
    CAPTURING = "CAPTURING"
    THINKING = "THINKING"
    RESPONDING = "RESPONDING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


@dataclass
class SessionState:
    """SessionState data structure exactly as specified in section 3"""
    id: str
    phase: SessionPhase = SessionPhase.IDLE
    pcm_buffer: bytearray = field(default_factory=bytearray)  # Int16 LE mono 16k
    last_partial_text: str = ""
    last_partial_emit_ts: float = 0.0
    speech_started: bool = False
    last_speech_frame_ts: float = 0.0
    vad_silence_ms: int = 0
    llm_task: Optional[asyncio.Task] = None
    tts_tasks: List[asyncio.Task] = field(default_factory=list)
    cancellation_flag: bool = False
    phrase_queue_len: int = 0
    # Step 2 additions
    buffer_start_time: Optional[float] = None  # Track when first audio was received
    final_transcript_emitted: bool = False  # Track if final transcript was sent
    # Step 3 additions - ASR processing state
    whisper_model: Optional['WhisperModel'] = None  # faster-whisper model instance
    asr_task: Optional[asyncio.Task] = None  # Current ASR transcription task
    last_partial_time: float = 0.0  # Last time partial transcript was emitted
    transcript_segments: List[str] = field(default_factory=list)  # Store transcript segments
    current_transcript_id: Optional[str] = None  # Current transcript ID
    metrics: Dict[str, Optional[float]] = field(default_factory=lambda: {
        't0_audio_start': None,
        't1_first_partial': None,
        't2_final_transcript': None,
        't3_first_llm_token': None,
        't4_first_tts_audio': None
    })


# Error codes as specified in section 5
class ErrorCode:
    """Error codes for v2 implementation"""
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    INTERNAL = "INTERNAL"
    CANCELLED = "CANCELLED"
    MAX_DURATION_EXCEEDED = "MAX_DURATION_EXCEEDED"
    ASR_TIMEOUT = "ASR_TIMEOUT"
    ASR_FAIL = "ASR_FAIL"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_FAIL = "LLM_FAIL"
    TTS_FAIL = "TTS_FAIL"
    TTS_TIMEOUT = "TTS_TIMEOUT"


class MessageType:
    """Message type constants for WebSocket protocol"""
    START = "start"
    STOP = "stop"
    CANCEL = "cancel"
    ERROR = "error"
    INFO = "info"
    PARTIAL_TRANSCRIPT = "partial_transcript"
    FINAL_TRANSCRIPT = "final_transcript"
    LLM_TOKEN = "llm_token"
    TTS_CHUNK = "tts_chunk"
    TTS_PHRASE_DONE = "tts_phrase_done"
    TTS_COMPLETE = "tts_complete"


# WebSocket Protocol Message Models for v2
class StartMessage(BaseModel):
    """Start message format"""
    type: str = "start"
    sample_rate: int


class StopMessage(BaseModel):
    """Stop message format"""
    type: str = "stop"


class CancelMessage(BaseModel):
    """Cancel message format"""
    type: str = "cancel"


class ErrorMessage(BaseModel):
    """Error message format"""
    type: str = "error"
    code: str
    message: str
    recoverable: bool


class InfoMessage(BaseModel):
    """Info message format"""
    type: str = "info"
    message: str


class PartialTranscriptMessage(BaseModel):
    """Partial transcript message format"""
    type: str = "partial_transcript"
    text: str


class FinalTranscriptMessage(BaseModel):
    """Final transcript message format"""
    type: str = "final_transcript"
    text: str


class LLMTokenMessage(BaseModel):
    """LLM token message format"""
    type: str = "llm_token"
    text: str
    done: bool


class TTSChunkMessage(BaseModel):
    """TTS chunk message format"""
    type: str = "tts_chunk"
    seq: int
    audio_b64: str
    mime: str


class TTSPhraseDoneMessage(BaseModel):
    """TTS phrase done message format"""
    type: str = "tts_phrase_done"
    seq: int


class TTSCompleteMessage(BaseModel):
    """TTS complete message format"""
    type: str = "tts_complete"


class TranscriptMessage(BaseModel):
    """Transcript message format (for backward compatibility)"""
    type: str
    text: str


class ConversationMemory:
    """Manages conversation history for LLM context"""

    def __init__(self, max_context_messages: int = 10):
        self.max_context_messages = max_context_messages
        self.messages: List[Dict[str, str]] = []

    def add_user_message(self, content: str):
        """Add a user message to the conversation history"""
        self.messages.append({"role": "user", "content": content})
        self._trim_context()

    def add_assistant_message(self, content: str):
        """Add an assistant message to the conversation history"""
        self.messages.append({"role": "assistant", "content": content})
        self._trim_context()

    def _trim_context(self):
        """Trim conversation history to maintain context limit"""
        if len(self.messages) > self.max_context_messages:
            # Keep the most recent messages
            self.messages = self.messages[-self.max_context_messages:]

    def get_messages_for_llm(self) -> List[Dict[str, str]]:
        """Get messages formatted for LLM API"""
        return self.messages.copy()

    def clear(self):
        """Clear all conversation history"""
        self.messages.clear()


class StreamingSession:
    """WebSocket session management for v2"""

    def __init__(self, websocket: WebSocket, settings: StreamingSettings):
        self.websocket = websocket
        self.settings = settings
        self.session_state = SessionState(id=str(id(self)))
        self.is_active = False

    async def send_message(self, message):
        """Send a message to the client"""
        try:
            if isinstance(message, dict):
                await self.websocket.send_json(message)
            else:
                await self.websocket.send_text(str(message))
        except WebSocketDisconnect:
            logger.warning(f"WebSocket disconnected for session {self.session_state.id}")
            self.is_active = False
        except Exception as e:
            logger.error(f"Error sending message for session {self.session_state.id}: {e}")
            self.is_active = False

    async def send_error(self, code: str, message: str, recoverable: bool = True):
        """Send an error message to the client"""
        error_msg = ErrorMessage(code=code, message=message, recoverable=recoverable)
        await self.send_message(error_msg.dict())

    async def send_info(self, message: str):
        """Send an info message to the client"""
        info_msg = InfoMessage(message=message)
        await self.send_message(info_msg.dict())

    def can_start(self) -> bool:
        """Check if session can transition to CAPTURING"""
        return self.session_state.phase == SessionPhase.IDLE

    def can_cancel(self) -> bool:
        """Check if session can be cancelled"""
        return self.session_state.phase != SessionPhase.COMPLETE and self.session_state.phase != SessionPhase.ERROR

    async def handle_start(self, sample_rate: int):
        """Handle start message"""
        if not self.can_start():
            await self.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                f"Cannot start session in {self.session_state.phase.value} state",
                recoverable=False
            )
            return False

        # Update session state
        self.session_state.phase = SessionPhase.CAPTURING
        self.is_active = True

        logger.info(f"Session {self.session_state.id} started with sample rate {sample_rate}")
        await self.send_info(f"Session started - ready to receive audio data")
        return True

    async def handle_stop(self):
        """Handle stop message"""
        if self.session_state.phase == SessionPhase.IDLE:
            await self.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                "Cannot stop session that is not started",
                recoverable=False
            )
            return False

        # Trigger finalization due to stop message
        await self._finalize_session("STOP_MESSAGE")

        # Update session state to COMPLETE after finalization
        old_phase = self.session_state.phase
        self.session_state.phase = SessionPhase.COMPLETE
        self.is_active = False

        logger.info(f"Session {self.session_state.id} stopped: {old_phase.value} -> {self.session_state.phase.value}")
        await self.send_info("Session stopped")
        return True

    async def handle_cancel(self):
        """Handle cancel message"""
        if not self.can_cancel():
            await self.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                f"Cannot cancel session in {self.session_state.phase.value} state",
                recoverable=False
            )
            return False

        # Trigger finalization due to cancellation
        await self._finalize_session("CANCEL_MESSAGE")

        # Update session state
        old_phase = self.session_state.phase
        self.session_state.phase = SessionPhase.COMPLETE
        self.session_state.cancellation_flag = True
        self.is_active = False

        # Cancel any running tasks
        if self.session_state.llm_task and not self.session_state.llm_task.done():
            self.session_state.llm_task.cancel()

        for task in self.session_state.tts_tasks:
            if not task.done():
                task.cancel()

        # Cancel ASR task (Step 3)
        if self.session_state.asr_task and not self.session_state.asr_task.done():
            self.session_state.asr_task.cancel()

        logger.info(f"Session {self.session_state.id} cancelled: {old_phase.value} -> {self.session_state.phase.value}")
        await self.send_info("Session cancelled")
        return True

    async def handle_binary_data(self, data: bytes):
        """Handle incoming binary PCM data"""
        # Reject binary frames before start
        if self.session_state.phase != SessionPhase.CAPTURING:
            await self.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                f"Audio frames only accepted in CAPTURING state (current: {self.session_state.phase.value})",
                recoverable=False
            )
            return

        # Hard cap per-frame size 64 KB
        MAX_FRAME_SIZE = 64 * 1024  # 64 KB
        if len(data) > MAX_FRAME_SIZE:
            await self.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                f"Binary frame too large: {len(data)} bytes (max: {MAX_FRAME_SIZE})",
                recoverable=False
            )
            return

        # Check buffer size before adding new data
        if not self._check_buffer_size_limit(data):
            return

        # Append to PCM buffer
        self.session_state.pcm_buffer.extend(data)

        # Track buffer start time for duration calculation
        if self.session_state.buffer_start_time is None:
            self.session_state.buffer_start_time = time.time()

        # Track metrics
        if self.session_state.metrics['t0_audio_start'] is None:
            self.session_state.metrics['t0_audio_start'] = time.time()

        logger.debug(f"Session {self.session_state.id} received {len(data)} bytes of audio data")

    def _check_buffer_size_limit(self, new_data: bytes) -> bool:
        """Check if adding new data would exceed buffer size limit"""
        # Calculate current buffer duration in milliseconds
        current_duration_ms = self._get_buffer_duration_ms()

        # Calculate max allowed bytes: (STREAM_MAX_UTTERANCE_MS/1000)*16000*2 + 4% overhead
        max_duration_ms = self.settings.stream_max_utterance_ms
        max_bytes = int((max_duration_ms / 1000.0) * 16000 * 2 * 1.04)  # +4% overhead

        # Check if current buffer + new data would exceed limit
        would_exceed = len(self.session_state.pcm_buffer) + len(new_data) > max_bytes

        if would_exceed:
            logger.warning(f"Session {self.session_state.id} buffer size limit would be exceeded: "
                         f"{len(self.session_state.pcm_buffer) + len(new_data)} > {max_bytes} bytes")

            # Trigger finalization due to max duration exceeded
            asyncio.create_task(self._finalize_session("MAX_DURATION_EXCEEDED"))
            return False

        return True

    def _get_buffer_duration_ms(self) -> float:
        """Get current buffer duration in milliseconds"""
        if not self.session_state.pcm_buffer or self.session_state.buffer_start_time is None:
            return 0.0

        # Calculate duration: bytes / (sample_rate * bytes_per_sample)
        # 16000 samples/sec * 2 bytes/sample = 32000 bytes/sec
        bytes_per_ms = (16000 * 2) / 1000  # 32 bytes per millisecond
        duration_ms = len(self.session_state.pcm_buffer) / bytes_per_ms
        return duration_ms


class LLMStreamingService:
    """Handles LLM streaming with OpenAI API and local fallback (Phase 2)"""

    def __init__(self, settings: StreamingSettings):
        self.settings = settings
        self.conversation_memory = ConversationMemory()
        self.current_streaming_task = None
        self.is_streaming = False
        self.streaming_start_time = None
        self.last_token_time = None

        # Import OpenAI only if API key is provided
        self.openai_available = bool(settings.openai_api_key)
        if self.openai_available:
            try:
                import openai
                self.openai_client = openai.OpenAI(api_key=settings.openai_api_key)
                logger.info("OpenAI client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}")
                self.openai_available = False
        else:
            logger.info("No OpenAI API key provided, using local fallback mode")

    def _check_overall_timeout(self) -> bool:
        """Check if overall streaming timeout has been exceeded"""
        if not self.streaming_start_time:
            return False

        elapsed = time.time() - self.streaming_start_time
        timeout_seconds = self.settings.stream_llm_timeout_s

        if elapsed > timeout_seconds:
            logger.warning(f"LLM streaming overall timeout exceeded: {elapsed:.1f}s > {timeout_seconds}s")
            return True
        return False

    def _check_token_timeout(self) -> bool:
        """Check if token timeout (5 seconds) has been exceeded"""
        if not self.last_token_time:
            return False

        elapsed = time.time() - self.last_token_time
        token_timeout_seconds = 5.0

        if elapsed > token_timeout_seconds:
            logger.warning(f"LLM token timeout exceeded: {elapsed:.1f}s > {token_timeout_seconds}s")
            return True
        return False

    def _update_token_time(self):
        """Update the last token timestamp"""
        self.last_token_time = time.time()

    async def _handle_timeout_error(self, websocket_session, timeout_type: str):
        """Handle timeout errors by sending error message and aborting"""
        if timeout_type == "overall":
            error_code = ErrorCode.LLM_TIMEOUT
            message = f"LLM streaming timeout after {self.settings.stream_llm_timeout_s}s"
        else:
            error_code = ErrorCode.LLM_TIMEOUT
            message = "No LLM tokens received for 5 seconds"

        await websocket_session.send_error(error_code, message, recoverable=False)

        # Cancel the current streaming operation
        await self.cancel_current_streaming()

    async def stream_llm_response(self, user_input: str, websocket_session) -> str:
        """
        Stream LLM response for user input
        Returns the complete response text for conversation memory
        """
        try:
            # Check if already streaming and cancel if needed
            if self.is_streaming:
                logger.warning("LLM streaming already in progress, cancelling previous operation")
                await self.cancel_current_streaming()

            # Set streaming state
            self.is_streaming = True
            self.current_streaming_task = asyncio.current_task()
            self.streaming_start_time = time.time()
            self.last_token_time = time.time()  # Initialize token time

            # Store user message for later memory update (memory gating)
            temp_user_message = user_input
            complete_response = ""

            # Start timeout checker in background
            timeout_checker_task = asyncio.create_task(
                self._start_timeout_checker(websocket_session)
            )

            try:
                if self.openai_available and not self.settings.llm_use_local_fallback:
                    # Use OpenAI streaming API
                    complete_response = await self._stream_openai_response(
                        temp_user_message, websocket_session
                    )
                else:
                    # Use local fallback
                    complete_response = await self._stream_local_fallback(
                        temp_user_message, websocket_session
                    )

                # Only add to conversation memory after complete response (memory gating)
                if complete_response:
                    self.conversation_memory.add_user_message(temp_user_message)
                    self.conversation_memory.add_assistant_message(complete_response)

                return complete_response

            except asyncio.CancelledError:
                logger.info("LLM streaming was cancelled")
                raise
            except Exception as e:
                logger.error(f"Error in LLM streaming: {e}")
                await websocket_session.send_error(ErrorCode.LLM_FAIL, f"LLM streaming error: {str(e)}", recoverable=False)
                return ""

        finally:
            # Cancel timeout checker if still running
            if 'timeout_checker_task' in locals():
                timeout_checker_task.cancel()

            # Clear streaming state
            self.is_streaming = False
            self.current_streaming_task = None
            self.streaming_start_time = None
            self.last_token_time = None

    async def _stream_openai_response(self, user_input: str, websocket_session) -> str:
        """Stream response using OpenAI API"""
        try:
            # Prepare messages for OpenAI
            messages = self.conversation_memory.get_messages_for_llm()
            messages.append({"role": "user", "content": user_input})

            # Create streaming completion
            stream = self.openai_client.chat.completions.create(
                model=self.settings.openai_model,
                messages=messages,
                max_tokens=self.settings.openai_max_tokens,
                temperature=self.settings.openai_temperature,
                stream=True
            )

            complete_response = ""
            current_token = ""
            first_token = True

            # Process streaming response
            async for chunk in stream:
                # Check for timeouts before processing each chunk
                if self._check_overall_timeout():
                    await self._handle_timeout_error(websocket_session, "overall")
                    return ""

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                if delta.content:
                    current_token += delta.content
                    complete_response += delta.content

                    # Update token timestamp on first token and periodically
                    if first_token or len(current_token) > 10:
                        self._update_token_time()
                        first_token = False

                    # Send token fragment
                    token_msg = LLMTokenMessage(
                        text=current_token,
                        done=False
                    )
                    await websocket_session.send_message(token_msg.dict())

                    # Track t3: first LLM token sent (Phase 4)
                    if websocket_session.timestamps['t3_first_llm_token'] is None:
                        websocket_session.timestamps['t3_first_llm_token'] = time.time()
                        logger.info(f"[Latency] t3_first_llm_token: {websocket_session.timestamps['t3_first_llm_token']}")
                        websocket_session.log_latency_deltas()

                    # Process token through TTS service (Phase 3)
                    await websocket_session.tts_service.process_llm_token(current_token, websocket_session)

                    # Reset current token for next batch
                    current_token = ""

                # Check if this is the final chunk
                if chunk.choices[0].finish_reason:
                    break

            # Send final message
            final_msg = LLMTokenMessage(done=True)
            await websocket_session.send_message(final_msg.dict())

            # Finalize TTS for end of response (Phase 3)
            await websocket_session.tts_service.finalize_response(websocket_session)

            return complete_response

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            raise

    async def _stream_local_fallback(self, user_input: str, websocket_session) -> str:
        """Local fallback that yields word-by-word for testing"""
        try:
            # Simple fallback response (in real implementation, this could use a local model)
            fallback_response = f"Thank you for saying: '{user_input}'. This is a fallback response from the local LLM service."

            # Split into words for streaming
            words = fallback_response.split()

            for i, word in enumerate(words):
                # Check for timeouts before sending each token
                if self._check_overall_timeout():
                    await self._handle_timeout_error(websocket_session, "overall")
                    return ""

                # Send each word as a token
                token_msg = LLMTokenMessage(
                    text=word + " ",
                    done=False
                )
                await websocket_session.send_message(token_msg.dict())

                # Update token timestamp
                self._update_token_time()

                # Update metrics (Phase 4)
                websocket_session.metrics['llm_tokens_generated'] += 1

                # Process token through TTS service (Phase 3)
                await websocket_session.tts_service.process_llm_token(word + " ", websocket_session)

                # Small delay to simulate streaming (remove in production)
                await asyncio.sleep(0.1)

            # Send final message
            final_msg = LLMTokenMessage(done=True)
            await websocket_session.send_message(final_msg.dict())

            # Finalize TTS for end of response (Phase 3)
            await websocket_session.tts_service.finalize_response(websocket_session)

            return fallback_response

        except Exception as e:
            logger.error(f"Local fallback streaming error: {e}")
            raise

    async def cancel_current_streaming(self):
        """Cancel the current LLM streaming operation"""
        try:
            if self.is_streaming and self.current_streaming_task:
                logger.info("Cancelling current LLM streaming operation")

                # Cancel the current task
                if not self.current_streaming_task.done():
                    self.current_streaming_task.cancel()

                    try:
                        # Wait for cancellation to complete
                        await asyncio.wait_for(self.current_streaming_task, timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.warning("LLM streaming cancellation timed out")
                    except asyncio.CancelledError:
                        logger.info("LLM streaming cancelled successfully")

                # Clear streaming state
                self.is_streaming = False
                self.current_streaming_task = None
                self.streaming_start_time = None
                self.last_token_time = None

                logger.info("LLM streaming operation cancelled")

        except Exception as e:
            logger.error(f"Error cancelling LLM streaming: {e}")

    async def _start_timeout_checker(self, websocket_session):
        """Start a background task to check for timeouts"""
        try:
            while self.is_streaming:
                # Check overall timeout
                if self._check_overall_timeout():
                    await self._handle_timeout_error(websocket_session, "overall")
                    break

                # Check token timeout (5 seconds without tokens)
                if self._check_token_timeout():
                    await self._handle_timeout_error(websocket_session, "token")
                    break

                # Wait 1 second before next check
                await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            logger.info("Timeout checker cancelled")
        except Exception as e:
            logger.error(f"Error in timeout checker: {e}")


class PhraseAggregator:
    """Accumulates LLM tokens into phrases for TTS synthesis"""

    def __init__(self, max_phrase_length: int = 60, punctuation_chars: str = ".?!", token_gap_ms: int = 700):
        self.max_phrase_length = max_phrase_length
        self.punctuation_chars = set(punctuation_chars)
        self.token_gap_ms = token_gap_ms
        self.current_phrase = ""
        self.phrase_queue = []
        self.next_seq = 0
        self.last_token_time = None

    def add_token(self, token: str) -> Optional[str]:
        """
        Add a token to the current phrase and return a complete phrase if ready
        Returns None if phrase is not complete, or the phrase text if it should be synthesized
        """
        current_time = time.time()

        # Check for token gap timeout if we have a previous token
        if (self.last_token_time and self.current_phrase and
            (current_time - self.last_token_time) * 1000 >= self.token_gap_ms):
            # Token gap exceeded, complete current phrase
            phrase = self.current_phrase.strip()
            self.current_phrase = ""
            self.last_token_time = None
            return phrase

        # Add token to current phrase
        if self.current_phrase and not self.current_phrase.endswith(' '):
            self.current_phrase += ' '
        self.current_phrase += token.strip()

        # Update last token time
        self.last_token_time = current_time

        # Check if phrase should be completed
        if self._should_complete_phrase():
            phrase = self.current_phrase.strip()
            self.current_phrase = ""
            self.last_token_time = None
            return phrase

        return None

    def _should_complete_phrase(self) -> bool:
        """Check if current phrase should be completed based on punctuation or length"""
        if not self.current_phrase:
            return False

        # Check for punctuation at the end
        if self.current_phrase[-1] in self.punctuation_chars:
            return True

        # Check length threshold
        if len(self.current_phrase) > self.max_phrase_length:
            return True

        return False

    def force_complete_phrase(self) -> Optional[str]:
        """Force completion of current phrase (for end of response)"""
        if not self.current_phrase:
            return None

        phrase = self.current_phrase.strip()
        self.current_phrase = ""
        return phrase

    def has_pending_phrase(self) -> bool:
        """Check if there's a current phrase being accumulated"""
        return bool(self.current_phrase.strip())

    def get_next_sequence_number(self) -> int:
        """Get the next sequence number for TTS phrases"""
        seq = self.next_seq
        self.next_seq += 1
        return seq


class TTSEngine:
    """Base class for TTS engines"""

    def __init__(self, engine_name: str):
        self.engine_name = engine_name
        self.is_initialized = False

    def initialize(self) -> bool:
        """Initialize the TTS engine. Returns True if successful."""
        raise NotImplementedError

    def synthesize_to_bytes(self, text: str) -> bytes:
        """Synthesize text to audio bytes. Returns WAV audio data."""
        raise NotImplementedError

    def get_supported_formats(self) -> list:
        """Get list of supported audio formats."""
        return ["wav"]

    def cleanup(self):
        """Cleanup resources."""
        pass


class Pyttsx3Engine(TTSEngine):
    """pyttsx3 TTS engine implementation"""

    def __init__(self):
        super().__init__("pyttsx3")
        self.engine = None

    def initialize(self) -> bool:
        """Initialize pyttsx3 engine"""
        try:
            import pyttsx3
            self.engine = pyttsx3.init()

            # Configure voice settings for better performance
            voices = self.engine.getProperty('voices')
            if voices:
                # Use first available voice
                self.engine.setProperty('voice', voices[0].id)

            # Set speech rate (words per minute)
            self.engine.setProperty('rate', 200)

            # Set volume (0.0 to 1.0)
            self.engine.setProperty('volume', 0.9)

            self.is_initialized = True
            logger.info("pyttsx3 TTS engine initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize pyttsx3 engine: {e}")
            self.is_initialized = False
            return False

    def synthesize_to_bytes(self, text: str) -> bytes:
        """Synthesize text to WAV bytes using pyttsx3"""
        if not self.is_initialized or not self.engine:
            raise RuntimeError("TTS engine not initialized")

        try:
            # Use a context manager for temporary file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                temp_filename = temp_file.name

            # Save audio to temp file
            self.engine.save_to_file(text, temp_filename)
            self.engine.runAndWait()

            # Read the generated file as bytes
            with open(temp_filename, 'rb') as audio_file:
                audio_bytes = audio_file.read()

            # Clean up temp file
            try:
                os.unlink(temp_filename)
            except:
                pass

            return audio_bytes

        except Exception as e:
            logger.error(f"Error synthesizing text with pyttsx3: {e}")
            raise

    def cleanup(self):
        """Cleanup pyttsx3 engine"""
        if self.engine:
            try:
                self.engine.stop()
                del self.engine
            except:
                pass
        self.is_initialized = False


class TTSStreamingService:
    """Handles phrase-based TTS synthesis with threading"""

    def __init__(self, settings: StreamingSettings):
        self.settings = settings
        self.phrase_aggregator = PhraseAggregator(
            max_phrase_length=self.settings.stream_tts_max_phrase_length,
            token_gap_ms=700  # Fixed 700ms as per requirements
        )
        self.tts_engine = None
        self.executor = None
        self.active_tasks = {}
        self.next_seq = 0
        self.is_processing = False
        self.pending_phrases_count = 0

        # Initialize TTS engine based on settings
        self._initialize_tts_engine()

        # Initialize thread pool
        self._initialize_thread_pool()

    def _initialize_tts_engine(self):
        """Initialize the appropriate TTS engine"""
        engine_name = self.settings.stream_tts_engine.lower()

        if engine_name == "pyttsx3":
            self.tts_engine = Pyttsx3Engine()
            if not self.tts_engine.initialize():
                logger.error("Failed to initialize pyttsx3 TTS engine")
                self.tts_engine = None
        else:
            logger.error(f"Unsupported TTS engine: {engine_name}")
            self.tts_engine = None

    def _initialize_thread_pool(self):
        """Initialize ThreadPoolExecutor for concurrent synthesis"""
        try:
            from concurrent.futures import ThreadPoolExecutor
            max_workers = self.settings.stream_tts_thread_workers
            self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="TTS")
            logger.info(f"TTS ThreadPoolExecutor initialized with max_workers={max_workers}")
        except Exception as e:
            logger.error(f"Failed to initialize TTS thread pool: {e}")
            self.executor = None

    async def process_llm_token(self, token: str, websocket_session) -> bool:
        """
        Process an LLM token and trigger TTS synthesis if phrase is complete
        Returns True if a phrase was completed and synthesis started
        """
        if not self.tts_engine or not self.executor:
            return False

        # Add token to phrase aggregator
        phrase = self.phrase_aggregator.add_token(token)

        if phrase:
            # Phrase is complete, start TTS synthesis
            seq = self.phrase_aggregator.get_next_sequence_number()
            await self._synthesize_phrase(phrase, seq, websocket_session)
            return True

        return False

    async def finalize_response(self, websocket_session):
        """Finalize TTS for end of LLM response"""
        if not self.tts_engine or not self.executor:
            return

        # Set processing state
        self.is_processing = True

        try:
            # Complete any remaining phrase
            final_phrase = self.phrase_aggregator.force_complete_phrase()
            if final_phrase:
                seq = self.phrase_aggregator.get_next_sequence_number()
                await self._synthesize_phrase(final_phrase, seq, websocket_session)

            # Wait for all active tasks to complete
            await self._wait_for_completion(websocket_session)

        finally:
            self.is_processing = False

    async def _synthesize_phrase(self, phrase: str, seq: int, websocket_session):
        """Synthesize a phrase using thread pool"""
        try:
            # Check pending phrases limit before starting new synthesis
            if self.pending_phrases_count >= self.settings.stream_max_pending_phrases:
                logger.warning(f"Phrase queue full ({self.pending_phrases_count}), dropping oldest phrase")

                # Find and cancel oldest pending phrase (drop oldest policy)
                if self.active_tasks:
                    oldest_seq = min(self.active_tasks.keys())
                    oldest_task_info = self.active_tasks[oldest_seq]

                    # Cancel the oldest task
                    oldest_task_info['future'].cancel()
                    del self.active_tasks[oldest_seq]
                    self.pending_phrases_count -= 1

                    logger.info(f"Dropped oldest phrase {oldest_seq} due to queue limit")
                    await websocket_session.send_error(
                        ErrorCode.TTS_FAIL,
                        f"Phrase queue full, dropped oldest phrase {oldest_seq}",
                        recoverable=True
                    )

            # Submit synthesis task to thread pool
            future = self.executor.submit(self._synthesize_phrase_sync, phrase, seq)

            # Store task info
            self.active_tasks[seq] = {
                'future': future,
                'phrase': phrase,
                'start_time': time.time()
            }
            self.pending_phrases_count += 1

            # Add callback for when synthesis completes
            future.add_done_callback(
                lambda f: asyncio.create_task(
                    self._on_synthesis_complete(f, seq, websocket_session)
                )
            )

            logger.info(f"Started TTS synthesis for phrase {seq}: '{phrase[:50]}...' (pending: {self.pending_phrases_count})")

        except Exception as e:
            logger.error(f"Error starting TTS synthesis for phrase {seq}: {e}")
            await websocket_session.send_error(ErrorCode.TTS_FAIL, f"TTS synthesis error: {str(e)}", recoverable=True)

    def _synthesize_phrase_sync(self, phrase: str, seq: int) -> tuple:
        """Synchronous TTS synthesis (runs in thread pool)"""
        try:
            if not self.tts_engine:
                raise RuntimeError("TTS engine not available")

            # Synthesize audio
            audio_bytes = self.tts_engine.synthesize_to_bytes(phrase)

            # Encode to base64
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')

            return audio_b64, None  # None for error

        except Exception as e:
            logger.error(f"Error in TTS synthesis for phrase {seq}: {e}")
            return None, str(e)

    async def _on_synthesis_complete(self, future, seq: int, websocket_session):
        """Handle completion of TTS synthesis"""
        try:
            # Decrement pending phrases count
            self.pending_phrases_count = max(0, self.pending_phrases_count - 1)

            # Check for timeout
            current_time = time.time()
            if seq in self.active_tasks:
                task_info = self.active_tasks[seq]
                elapsed = current_time - task_info['start_time']
                timeout_seconds = self.settings.stream_tts_timeout_s

                if elapsed > timeout_seconds:
                    logger.warning(f"TTS synthesis timeout for phrase {seq}: {elapsed:.2f}s > {timeout_seconds}s")
                    await websocket_session.send_error(ErrorCode.TTS_TIMEOUT, f"TTS timeout for phrase {seq} after {elapsed:.1f}s", recoverable=True)

                    # Remove from active tasks
                    del self.active_tasks[seq]
                    return

            # Get result from future
            try:
                result = future.result(timeout=0.1)  # Short timeout since we're in callback
            except Exception as e:
                logger.error(f"TTS synthesis failed for phrase {seq}: {e}")
                await websocket_session.send_error(ErrorCode.TTS_FAIL, f"TTS synthesis failed for phrase {seq}: {str(e)}", recoverable=True)

                # Remove from active tasks
                if seq in self.active_tasks:
                    del self.active_tasks[seq]
                return

            if seq in self.active_tasks:
                task_info = self.active_tasks[seq]
                phrase = task_info['phrase']

                if result and len(result) == 2:
                    audio_b64, error = result

                    if error:
                        logger.error(f"TTS synthesis failed for phrase {seq}: {error}")
                        await websocket_session.send_error(ErrorCode.TTS_FAIL, f"TTS error for phrase {seq}: {error}", recoverable=True)
                    else:
                        # Send audio chunk
                        tts_msg = TTSChunkMessage(
                            seq=seq,
                            audio_b64=audio_b64,
                            mime="audio/wav"
                        )
                        await websocket_session.send_message(tts_msg.dict())

                        # Track t4: first TTS audio sent (Phase 4)
                        if websocket_session.timestamps['t4_first_tts_audio'] is None:
                            websocket_session.timestamps['t4_first_tts_audio'] = time.time()
                            logger.info(f"[Latency] t4_first_tts_audio: {websocket_session.timestamps['t4_first_tts_audio']}")
                            websocket_session.log_latency_deltas()
                
                            # Log structured latency metrics if all timestamps are available (Phase 4)
                            websocket_session.log_structured_latency_metrics()

                        # Send phrase completion
                        phrase_done_msg = TTSPhraseDoneMessage(seq=seq)
                        await websocket_session.send_message(phrase_done_msg.dict())

                        # Update metrics (Phase 4)
                        websocket_session.metrics['tts_chunks_generated'] += 1

                        elapsed = time.time() - task_info['start_time']
                        logger.info(f"TTS phrase {seq} completed in {elapsed:.2f}s: '{phrase[:50]}...' (queue size: {len(self.active_tasks)})")

                        # Log queue status for monitoring
                        if len(self.active_tasks) > 3:
                            logger.warning(f"High TTS queue size: {len(self.active_tasks)} active tasks")
                else:
                    logger.error(f"Invalid TTS result for phrase {seq}")
                    await websocket_session.send_error(ErrorCode.TTS_FAIL, f"Invalid TTS result for phrase {seq}", recoverable=True)

                # Remove from active tasks
                del self.active_tasks[seq]

        except Exception as e:
            logger.error(f"Error handling TTS completion for phrase {seq}: {e}")
            await websocket_session.send_error(ErrorCode.INTERNAL, f"TTS completion error: {str(e)}", recoverable=False)

    async def _wait_for_completion(self, websocket_session):
        """Wait for all active TTS tasks to complete"""
        if not self.active_tasks:
            return

        try:
            # Wait for all futures to complete
            for task_info in self.active_tasks.values():
                future = task_info['future']
                try:
                    # Wait with timeout
                    result = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, future.result),
                        timeout=30.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"TTS task timeout for phrase {task_info.get('seq', 'unknown')}")
                except Exception as e:
                    logger.error(f"Error waiting for TTS task: {e}")

            # Send final completion message
            complete_msg = TTSCompleteMessage()
            await websocket_session.send_message(complete_msg.dict())

            logger.info("All TTS tasks completed")

        except Exception as e:
            logger.error(f"Error in TTS completion wait: {e}")
            await websocket_session.send_error(f"TTS completion error: {str(e)}")

    def cleanup(self):
        """Cleanup TTS service resources"""
        # Cancel active tasks
        for task_info in self.active_tasks.values():
            future = task_info['future']
            if not future.done():
                future.cancel()

        self.active_tasks.clear()

        # Clear phrase aggregator state
        self.phrase_aggregator.current_phrase = ""
        self.phrase_aggregator.phrase_queue.clear()

        # Reset processing state
        self.is_processing = False

        # Reset pending phrases count
        self.pending_phrases_count = 0

        # Shutdown thread pool
        if self.executor:
            self.executor.shutdown(wait=True, timeout=5.0)

        # Cleanup TTS engine
        if self.tts_engine:
            self.tts_engine.cleanup()

    async def cancel_all_operations(self):
        """Cancel all ongoing TTS operations"""
        try:
            logger.info(f"Cancelling TTS operations - {len(self.active_tasks)} active tasks")

            # Cancel all active futures
            for seq, task_info in list(self.active_tasks.items()):
                future = task_info['future']
                if not future.done():
                    future.cancel()

                    # Remove from active tasks immediately
                    del self.active_tasks[seq]

                    logger.debug(f"Cancelled TTS task {seq}")

            # Clear phrase aggregator state
            self.phrase_aggregator.current_phrase = ""
            self.phrase_aggregator.phrase_queue.clear()

            # Reset processing state
            self.is_processing = False

            logger.info("All TTS operations cancelled")

        except Exception as e:
            logger.error(f"Error cancelling TTS operations: {e}")


class StreamingSession:
    """Session state management for WebSocket connections"""

    def __init__(self, websocket: WebSocket, settings: StreamingSettings):
        self.websocket = websocket
        self.session_id = id(self)
        self.settings = settings
        self.is_active = False
        self.is_cancelled = False  # Track cancellation state separately
        self.state = SessionState.IDLE  # Session state management

        # Audio buffer management
        self.pcm_buffer = bytearray()
        self.rolling_buffer = deque(maxlen=settings.stream_pcm_chunk_size * 10)  # 10 seconds max
        self.sample_rate = settings.stream_vad_sample_rate

        # Latency tracking timestamps (Phase 4)
        self.timestamps = {
            't0_audio_start': None,  # First audio sample received
            't1_first_partial': None,  # First partial transcript sent
            't2_final_transcript': None,  # Final transcript sent
            't3_first_llm_token': None,  # First LLM token sent
            't4_first_tts_audio': None  # First TTS audio sent
        }

        # Server-side metrics (Phase 4)
        self.metrics = {
            'audio_frames_received': 0,
            'total_samples_received': 0,
            'transcripts_generated': 0,
            'llm_tokens_generated': 0,
            'tts_chunks_generated': 0,
            'asr_processing_time': 0.0,
            'llm_processing_time': 0.0,
            'tts_processing_time': 0.0,
            'session_start_time': time.time()
        }

        # VAD state and processing
        self.vad = webrtcvad.Vad(3)  # Aggressiveness level 3 (highest)
        self.vad_frame_size = int(settings.stream_vad_sample_rate * settings.stream_vad_frame_ms / 1000)
        self.silence_frames = 0
        self.total_silence_ms = 0
        self.is_speaking = False
        self.speech_start_time = 0.0

        # Transcription state
        self.whisper_model = None
        self.last_partial_time = 0.0
        self.transcript_segments = []
        self.current_transcript_id = None

        # Threading for async transcription
        self.transcription_lock = threading.Lock()

        # Backpressure state (Phase 4)
        self.backpressure_active = False
        self.backpressure_frame_count = 0
        self.backpressure_info_sent = False

        # LLM streaming service (Phase 2)
        self.llm_service = LLMStreamingService(settings)

        # TTS streaming service (Phase 3)
        self.tts_service = TTSStreamingService(settings)

        # Initialize Whisper model (Step 3)
        self._initialize_whisper_model()

    def log_latency_deltas(self):
        """Log latency deltas between timestamps (Phase 4)"""
        t = self.timestamps

        if t['t0_audio_start'] is not None:
            if t['t1_first_partial'] is not None:
                delta_t1_t0 = (t['t1_first_partial'] - t['t0_audio_start']) * 1000
                logger.info(f"[Latency] Δ t1-t0 (ASR latency): {delta_t1_t0:.2f}ms")

            if t['t2_final_transcript'] is not None:
                delta_t2_t0 = (t['t2_final_transcript'] - t['t0_audio_start']) * 1000
                logger.info(f"[Latency] Δ t2-t0 (Final ASR latency): {delta_t2_t0:.2f}ms")

            if t['t3_first_llm_token'] is not None:
                delta_t3_t0 = (t['t3_first_llm_token'] - t['t0_audio_start']) * 1000
                logger.info(f"[Latency] Δ t3-t0 (LLM start latency): {delta_t3_t0:.2f}ms")

            if t['t4_first_tts_audio'] is not None:
                delta_t4_t0 = (t['t4_first_tts_audio'] - t['t0_audio_start']) * 1000
                logger.info(f"[Latency] Δ t4-t0 (End-to-end latency): {delta_t4_t0:.2f}ms")

        # Calculate RTF (Real-time factor) for audio processing (Phase 4)
        if self.metrics['total_samples_received'] > 0:
            audio_duration = self.metrics['total_samples_received'] / self.sample_rate
            session_duration = time.time() - self.metrics['session_start_time']
            if session_duration > 0:
                rtf = audio_duration / session_duration
                logger.info(f"[RTF] Real-time factor: {rtf:.3f}x ({'faster' if rtf < 1 else 'slower'} than real-time)")

    def log_structured_latency_metrics(self):
        """Log structured latency metrics in JSON format (Phase 4)"""
        # Only log if latency logging is enabled
        if not self.settings.stream_log_latency:
            return

        t = self.timestamps

        # Check if we have all required timestamps
        if not all([t['t0_audio_start'], t['t1_first_partial'], t['t2_final_transcript'],
                   t['t3_first_llm_token'], t['t4_first_tts_audio']]):
            return

        # Calculate latency metrics in milliseconds
        t_first_partial_ms = (t['t1_first_partial'] - t['t0_audio_start']) * 1000
        t_final_transcript_ms = (t['t2_final_transcript'] - t['t0_audio_start']) * 1000
        t_first_token_ms = (t['t3_first_llm_token'] - t['t0_audio_start']) * 1000
        t_first_audio_ms = (t['t4_first_tts_audio'] - t['t0_audio_start']) * 1000

        # Calculate total reply time (from first audio to last TTS audio)
        total_reply_ms = (t['t4_first_tts_audio'] - t['t0_audio_start']) * 1000

        # Create structured latency log
        latency_data = {
            "event": "latency",
            "session_id": self.session_id,
            "t_first_partial_ms": round(t_first_partial_ms, 2),
            "t_final_transcript_ms": round(t_final_transcript_ms, 2),
            "t_first_token_ms": round(t_first_token_ms, 2),
            "t_first_audio_ms": round(t_first_audio_ms, 2),
            "total_reply_ms": round(total_reply_ms, 2),
            "timestamp": time.time()
        }

        # Log as structured JSON
        logger.info(f"[LATENCY_METRICS] {latency_data}")

        # Also log individual deltas for backward compatibility
        logger.info(f"[Latency] Session {self.session_id} - Partial: {t_first_partial_ms:.2f}ms, "
                   f"Final: {t_final_transcript_ms:.2f}ms, Token: {t_first_token_ms:.2f}ms, "
                   f"Audio: {t_first_audio_ms:.2f}ms, Total: {total_reply_ms:.2f}ms")

    def log_comprehensive_metrics(self):
        """Log comprehensive session metrics (Phase 4)"""
        logger.info(f"[Metrics] Session {self.session_id} - Audio frames: {self.metrics['audio_frames_received']}, "
                   f"Samples: {self.metrics['total_samples_received']}")
        logger.info(f"[Metrics] Session {self.session_id} - Transcripts: {self.metrics['transcripts_generated']}, "
                   f"LLM tokens: {self.metrics['llm_tokens_generated']}, TTS chunks: {self.metrics['tts_chunks_generated']}")

        # Calculate processing rates
        session_duration = time.time() - self.metrics['session_start_time']
        if session_duration > 0:
            audio_rate = self.metrics['total_samples_received'] / session_duration
            transcript_rate = self.metrics['transcripts_generated'] / session_duration
            token_rate = self.metrics['llm_tokens_generated'] / session_duration
            tts_rate = self.metrics['tts_chunks_generated'] / session_duration

            logger.info(f"[Metrics] Session {self.session_id} - Rates: Audio {audio_rate:.1f} samples/s, "
                       f"Transcripts {transcript_rate:.3f}/s, Tokens {token_rate:.3f}/s, TTS {tts_rate:.3f}/s")

    async def send_message(self, message):
        """Send a message to the client"""
        try:
            if isinstance(message, dict):
                await self.websocket.send_json(message)
            else:
                await self.websocket.send_text(str(message))
        except WebSocketDisconnect:
            logger.warning(f"WebSocket disconnected for session {self.session_id}")
            self.is_active = False
        except Exception as e:
            logger.error(f"Error sending message for session {self.session_id}: {e}")
            self.is_active = False

    async def send_error(self, code: str, message: str, recoverable: bool = True):
        """Send an error message to the client"""
        error_msg = ErrorMessage(
            code=code,
            message=message,
            recoverable=recoverable
        )
        await self.send_message(error_msg.dict())

    def set_state(self, new_state: SessionState):
        """Set session state and log transition"""
        old_state = self.state
        self.state = new_state
        logger.info(f"Session {self.session_id} state: {old_state.value} -> {new_state.value}")

    def can_transition_to(self, target_state: SessionState) -> bool:
        """Check if state transition is valid"""
        valid_transitions = {
            SessionState.IDLE: [SessionState.CAPTURING],
            SessionState.CAPTURING: [SessionState.IDLE, SessionState.THINKING],
            SessionState.THINKING: [SessionState.RESPONDING, SessionState.COMPLETE],
            SessionState.RESPONDING: [SessionState.COMPLETE, SessionState.IDLE],
            SessionState.COMPLETE: [SessionState.IDLE]
        }
        return target_state in valid_transitions.get(self.state, [])

    def transition_to(self, target_state: SessionState) -> bool:
        """Attempt state transition, return True if successful"""
        if self.can_transition_to(target_state):
            self.set_state(target_state)
            return True
        else:
            logger.warning(f"Invalid state transition: {self.state.value} -> {target_state.value}")
            return False

    async def send_info(self, message: str):
        """Send an info message to the client"""
        info_msg = InfoMessage(message=message)
        await self.send_message(info_msg.dict())

    async def send_partial_transcript(self, text: str):
        """Send a partial transcript to the client"""
        # Track t1: first partial transcript sent (Step 3 metrics)
        if self.session_state.metrics['t1_first_partial'] is None:
            self.session_state.metrics['t1_first_partial'] = time.time()
            logger.info(f"[Latency] t1_first_partial: {self.session_state.metrics['t1_first_partial']}")

        # Update last partial text and timestamp
        self.session_state.last_partial_text = text
        self.session_state.last_partial_emit_ts = time.time()

        # Send partial transcript message
        partial_msg = PartialTranscriptMessage(text=text)
        await self.send_message(partial_msg.dict())

        logger.debug(f"Partial transcript emitted: '{text[:50]}...'")

    async def send_final_transcript(self, text: str, transcript_id: str = None):
        """Send a final transcript to the client"""
        # Track t2: final transcript sent (Step 3 metrics)
        if self.session_state.metrics['t2_final_transcript'] is None:
            self.session_state.metrics['t2_final_transcript'] = time.time()
            logger.info(f"[Latency] t2_final_transcript: {self.session_state.metrics['t2_final_transcript']}")

        # Mark final transcript as emitted
        self.session_state.final_transcript_emitted = True

        # Send final transcript message
        final_msg = FinalTranscriptMessage(text=text)
        await self.send_message(final_msg.dict())

        logger.info(f"Final transcript emitted: '{text[:50]}...' (id: {transcript_id})")

    def _should_emit_partial_transcript(self) -> bool:
        """Check if we should emit a partial transcript based on Option A algorithm"""
        current_time = time.time()

        # Check if enough time has passed since last partial
        time_since_last_partial = current_time - self.session_state.last_partial_time
        interval_seconds = self.settings.stream_partial_interval_ms / 1000.0

        if time_since_last_partial < interval_seconds:
            return False

        # Check if buffer duration is sufficient (>= 500ms)
        buffer_duration_ms = self._get_buffer_duration_ms()
        if buffer_duration_ms < 500:
            return False

        # Check if we're in CAPTURING phase
        if self.session_state.phase != SessionPhase.CAPTURING:
            return False

        return True

    def _get_buffer_duration_ms(self) -> float:
        """Get current buffer duration in milliseconds"""
        if not self.session_state.pcm_buffer or self.session_state.buffer_start_time is None:
            return 0.0

        # Calculate duration: bytes / (sample_rate * bytes_per_sample)
        # 16000 samples/sec * 2 bytes/sample = 32000 bytes/sec
        bytes_per_ms = (16000 * 2) / 1000  # 32 bytes per millisecond
        duration_ms = len(self.session_state.pcm_buffer) / bytes_per_ms
        return duration_ms

    def _initialize_whisper_model(self):
        """Initialize the Whisper ASR model"""
        try:
            logger.info(f"Loading Whisper model: {self.settings.stream_model_whisper}")
            self.session_state.whisper_model = WhisperModel(
                self.settings.stream_model_whisper,
                device="cpu",
                compute_type="int8"
            )
            logger.info("Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            self.session_state.whisper_model = None

    async def _run_async_transcription(self) -> str:
        """Run async transcription with timeout handling"""
        if not self.session_state.whisper_model or not self.session_state.pcm_buffer:
            return ""

        try:
            # Create transcription task with timeout
            transcription_task = asyncio.get_event_loop().run_in_executor(
                None,
                self._run_sync_transcription
            )

            # Wait for completion with timeout
            result = await asyncio.wait_for(
                transcription_task,
                timeout=self.settings.stream_asr_timeout_s
            )

            return result

        except asyncio.TimeoutError:
            logger.error(f"ASR transcription timeout after {self.settings.stream_asr_timeout_s}s")
            await self.send_error(ErrorCode.ASR_TIMEOUT, "ASR transcription timeout", recoverable=True)
            return ""
        except Exception as e:
            logger.error(f"ASR transcription error: {e}")
            await self.send_error(ErrorCode.ASR_FAIL, f"ASR transcription failed: {str(e)}", recoverable=True)
            return ""

    def _run_sync_transcription(self) -> str:
        """Run synchronous transcription (runs in thread pool)"""
        try:
            if not self.session_state.whisper_model or not self.session_state.pcm_buffer:
                return ""

            # Convert buffer to numpy array
            audio_array = self._get_pcm_array()

            if len(audio_array) == 0:
                return ""

            # Run transcription with faster-whisper
            segments, info = self.session_state.whisper_model.transcribe(
                audio_array,
                language="en",
                initial_prompt=None,
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.5,
                    min_speech_duration_ms=250,
                    max_speech_duration_s=30,
                    min_silence_duration_ms=500,
                    window_size_samples=1024,
                )
            )

            # Combine segments into full text
            transcript_parts = []
            for segment in segments:
                transcript_parts.append(segment.text.strip())

            return " ".join(transcript_parts).strip()

        except Exception as e:
            logger.error(f"Synchronous transcription error: {e}")
            raise

    def _get_pcm_array(self) -> np.ndarray:
        """Convert PCM buffer to numpy array for Whisper"""
        if not self.session_state.pcm_buffer:
            return np.array([], dtype=np.float32)

        # Convert bytes to float32 array
        # Convert 16-bit PCM to float32
        samples = np.frombuffer(self.session_state.pcm_buffer, dtype=np.int16).astype(np.float32) / 32768.0
        return samples

    def _is_valid_vad_frame(self, frame_bytes: bytes) -> bool:
        """Check if frame has valid size for VAD processing"""
        return len(frame_bytes) == self.vad_frame_size * 2  # 16-bit samples

    def _process_vad_frame(self, frame_bytes: bytes) -> bool:
        """Process a single VAD frame and return if voice is detected"""
        if not self._is_valid_vad_frame(frame_bytes):
            return False
        try:
            return self.vad.is_speech(frame_bytes, self.sample_rate)
        except Exception as e:
            logger.error(f"VAD processing error: {e}")
            return False

    def _update_silence_tracking(self, is_voice: bool):
        """Update silence tracking based on VAD result"""
        current_time = time.time()

        if is_voice:
            # Update last speech frame timestamp
            self.session_state.last_speech_frame_ts = current_time

            if not self.session_state.speech_started:
                # First speech frame detected - start speech
                self.session_state.speech_started = True
                logger.debug(f"Session {self.session_state.id} speech started")
            elif self.is_speaking:
                # Continue speaking - reset silence counter
                self.silence_frames = 0
                self.total_silence_ms = 0
            else:
                # Start speaking
                self.is_speaking = True
                self.speech_start_time = current_time
                self.silence_frames = 0
                self.total_silence_ms = 0
                logger.debug(f"Session {self.session_state.id} speech started")
        else:
            if self.is_speaking:
                # Update silence tracking
                self.silence_frames += 1
                frame_duration_ms = (self.vad_frame_size / self.sample_rate) * 1000
                self.total_silence_ms = self.silence_frames * frame_duration_ms

    def _should_emit_partial_transcript(self) -> bool:
        """Check if we should emit a partial transcript (with backpressure handling)"""
        current_time = time.time()

        # Check if enough time has passed since last partial
        time_based_check = (current_time - self.last_partial_time) >= (self.settings.stream_partial_interval_ms / 1000.0)

        if not time_based_check:
            return False

        # Check backpressure state (Phase 4)
        if self._is_under_backpressure():
            # During backpressure, skip frames according to configuration
            self.backpressure_frame_count += 1
            if self.backpressure_frame_count % (self.settings.stream_backpressure_skip_frames + 1) != 0:
                # Skip this frame
                if not self.backpressure_info_sent:
                    asyncio.create_task(self.send_info(f"Throttling partial transcripts due to backpressure (queue > {self.settings.stream_backpressure_threshold})"))
                    self.backpressure_info_sent = True
                return False

        # Check if backpressure has been relieved
        if not self._is_under_backpressure() and self.backpressure_active:
            self.backpressure_active = False
            self.backpressure_frame_count = 0
            self.backpressure_info_sent = False
            asyncio.create_task(self.send_info("Backpressure relieved, resuming normal transcript cadence"))

        return True

    def _is_under_backpressure(self) -> bool:
        """Check if WebSocket is under backpressure (Phase 4)"""
        try:
            # Check if WebSocket has a send queue and if it's over threshold
            # Note: This is a simplified check - in a real implementation you might need
            # to access WebSocket internal queue or use a different approach
            if hasattr(self.websocket, 'client_state') and hasattr(self.websocket, 'applications'):
                # This is a basic check - in production you might need more sophisticated queue monitoring
                # For now, we'll use a simple heuristic based on active operations
                active_operations = (
                    (self.is_speaking and self.metrics['transcripts_generated'] > 0) or
                    hasattr(self.llm_service, 'is_streaming') and self.llm_service.is_streaming or
                    (self.tts_service and self.tts_service.is_processing)
                )

                # If we have multiple active operations, consider it backpressure
                if active_operations:
                    # Simple heuristic: if we have TTS chunks being generated and LLM streaming,
                    # and we're in speaking state, we might be under pressure
                    tts_chunks = getattr(self.metrics, 'tts_chunks_generated', 0)
                    llm_tokens = getattr(self.metrics, 'llm_tokens_generated', 0)

                    # Consider backpressure if we have many TTS chunks queued and LLM is active
                    if tts_chunks > 10 and llm_tokens > 0:
                        return True

            return False

        except Exception as e:
            logger.error(f"Error checking backpressure state: {e}")
            return False

    def _check_backpressure_state(self):
        """Check and update backpressure state (Phase 4)"""
        under_pressure = self._is_under_backpressure()

        if under_pressure and not self.backpressure_active:
            self.backpressure_active = True
            logger.warning(f"Session {self.session_id} entering backpressure state")
        elif not under_pressure and self.backpressure_active:
            self.backpressure_active = False
            self.backpressure_frame_count = 0
            self.backpressure_info_sent = False
            logger.info(f"Session {self.session_id} backpressure relieved")

    def _should_end_speech(self) -> bool:
        """Check if speech should end based on silence duration"""
        # Check silence condition: now - last_speech_frame_ts >= STREAM_VAD_SILENCE_MS AND speech_started
        current_time = time.time()
        silence_duration_ms = (current_time - self.session_state.last_speech_frame_ts) * 1000

        silence_detected = (
            silence_duration_ms >= self.settings.stream_vad_silence_ms
            and self.session_state.speech_started
        )

        if silence_detected:
            logger.debug(f"Session {self.session_state.id} silence detected: {silence_duration_ms:.1f}ms >= {self.settings.stream_vad_silence_ms}ms")

        return silence_detected

    def _get_pcm_array(self) -> np.ndarray:
        """Convert rolling buffer to numpy array for Whisper"""
        if not self.rolling_buffer:
            return np.array([], dtype=np.float32)

        # Convert bytes to float32 array
        pcm_bytes = bytes(self.rolling_buffer)
        # Convert 16-bit PCM to float32
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return samples

    def _run_incremental_transcription(self) -> str:
        """Run incremental transcription on current buffer"""
        if not self.whisper_model or not self.rolling_buffer:
            return ""

        try:
            # Get PCM data as numpy array
            audio_array = self._get_pcm_array()

            if len(audio_array) == 0:
                return ""

            # Run transcription
            segments, info = self.whisper_model.transcribe(
                audio_array,
                language="en",
                initial_prompt=None,
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.5,
                    min_speech_duration_ms=250,
                    max_speech_duration_s=30,
                    min_silence_duration_ms=500,
                    window_size_samples=1024,
                )
            )

            # Combine segments
            transcript_parts = []
            for segment in segments:
                transcript_parts.append(segment.text.strip())

            return " ".join(transcript_parts).strip()

        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return ""

    async def _finalize_session(self, reason: str):
        """Finalize session and transition to THINKING state"""
        logger.info(f"Session {self.session_state.id} finalizing due to: {reason}")

        # Stop capturing frames
        self.is_active = False

        # Cancel any ongoing ASR task
        if self.session_state.asr_task and not self.session_state.asr_task.done():
            self.session_state.asr_task.cancel()
            try:
                await self.session_state.asr_task
            except asyncio.CancelledError:
                logger.debug("ASR task cancelled during finalization")

        # Emit final transcript (even if empty)
        final_text = await self._run_async_transcription()
        transcript_id = f"transcript_{self.session_state.id}_{int(time.time())}"

        if not self.session_state.final_transcript_emitted:
            await self.send_final_transcript(final_text, transcript_id)
            logger.info(f"Final transcript emitted: '{final_text[:50]}...' (reason: {reason})")

            # Track t2: final transcript sent (Step 3 metrics)
            if self.session_state.metrics['t2_final_transcript'] is None:
                self.session_state.metrics['t2_final_transcript'] = time.time()
        else:
            logger.debug(f"Session {self.session_state.id} final transcript already emitted")

        # Cleanup PCM buffer on finalization
        self._cleanup_pcm_buffer()

        # Transition to THINKING state
        if self.transition_to(SessionState.THINKING):
            logger.info(f"Session {self.session_state.id} transitioned to THINKING state")

            # Launch LLM streaming if transcript non-empty
            if final_text.strip():
                # Trigger LLM streaming within 300ms (FR4)
                asyncio.create_task(self._trigger_llm_streaming(final_text))
            else:
                logger.info(f"Session {self.session_state.id} empty transcript - skipping LLM streaming")
                # Transition directly to COMPLETE if no transcript
                self.transition_to(SessionState.COMPLETE)
        else:
            logger.error(f"Session {self.session_state.id} failed to transition to THINKING state")

    def _cleanup_pcm_buffer(self):
        """Cleanup PCM buffer and related state"""
        try:
            # Clear the PCM buffer
            self.session_state.pcm_buffer.clear()

            # Reset buffer start time
            self.session_state.buffer_start_time = None

            # Reset ASR-related state
            self.session_state.last_partial_text = ""
            self.session_state.last_partial_time = 0.0
            self.session_state.transcript_segments.clear()
            self.session_state.current_transcript_id = None

            logger.debug(f"Session {self.session_state.id} PCM buffer and ASR state cleaned up")

        except Exception as e:
            logger.error(f"Session {self.session_state.id} error during PCM buffer cleanup: {e}")

    def _cleanup_pcm_buffer(self):
        """Cleanup PCM buffer and related state"""
        try:
            # Clear the PCM buffer
            self.session_state.pcm_buffer.clear()

            # Clear rolling buffer
            self.rolling_buffer.clear()

            # Reset buffer start time
            self.session_state.buffer_start_time = None

            logger.debug(f"Session {self.session_state.id} PCM buffer cleaned up")

        except Exception as e:
            logger.error(f"Session {self.session_state.id} error during PCM buffer cleanup: {e}")

    async def _process_speech_end(self):
        """Process end of speech segment"""
        if not self.is_speaking:
            return

        # Trigger finalization due to silence
        await self._finalize_session("VAD_SILENCE")

        # Reset speech state
        self.is_speaking = False
        self.speech_start_time = 0.0
        self.silence_frames = 0
        self.total_silence_ms = 0
        self.transcript_segments = []

        # Reset VAD state as specified in requirements
        self.session_state.speech_started = False

    async def _process_partial_transcript(self):
        """Process and emit partial transcript using ASR Partial Diff Algorithm (Option A)"""
        if not self.is_speaking:
            return

        # Check if we should emit partial transcript based on Option A algorithm
        if not self._should_emit_partial_transcript():
            return

        current_time = time.time()

        # Run async transcription on full buffer
        partial_text = await self._run_async_transcription()

        if partial_text and partial_text.strip():
            # Check if text has changed from last partial
            if partial_text != self.session_state.last_partial_text:
                await self.send_partial_transcript(partial_text)
                self.session_state.last_partial_time = current_time
                logger.debug(f"Partial transcript emitted: '{partial_text[:50]}...'")
            else:
                logger.debug("Partial transcript unchanged, skipping emission")
        else:
            logger.debug("Empty partial transcript, skipping emission")

    def _run_incremental_transcription(self) -> str:
        """Legacy method for backward compatibility - now uses async transcription"""
        try:
            # Use asyncio to run the async transcription method
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running, we need to schedule this differently
                # For now, return empty string to avoid blocking
                logger.warning("Cannot run transcription in existing event loop")
                return ""
            else:
                return loop.run_until_complete(self._run_async_transcription())
        except Exception as e:
            logger.error(f"Error in legacy transcription method: {e}")
            return ""

    async def _trigger_llm_streaming(self, transcript_text: str):
        """Trigger LLM streaming after final transcript (Phase 2)"""
        try:
            # Ensure we start streaming within 300ms (FR4)
            await asyncio.sleep(0)  # Yield control to allow other operations

            logger.info(f"Starting LLM streaming for transcript: {transcript_text[:50]}...")

            # Transition to RESPONDING state when LLM streaming starts
            if not self.transition_to(SessionState.RESPONDING):
                logger.error("Failed to transition to RESPONDING state")
                return

            # Stream LLM response
            await self.llm_service.stream_llm_response(transcript_text, self)

            # Transition to COMPLETE state when LLM streaming finishes
            if not self.transition_to(SessionState.COMPLETE):
                logger.error("Failed to transition to COMPLETE state")

        except Exception as e:
            logger.error(f"Error triggering LLM streaming: {e}")
            await self.send_error(ErrorCode.INTERNAL, f"Failed to start LLM streaming: {str(e)}", recoverable=False)


# Global session management
active_sessions: Dict[str, StreamingSession] = {}

# Shutdown management
shutdown_event = asyncio.Event()
is_shutting_down = False


async def graceful_shutdown():
    """Handle graceful shutdown with session notification"""
    global is_shutting_down

    if is_shutting_down:
        logger.info("Shutdown already in progress")
        return

    logger.info("Initiating graceful shutdown...")
    is_shutting_down = True

    # Broadcast shutdown message to all active sessions
    await broadcast_shutdown_to_sessions()

    # Wait for sessions to close gracefully (up to 10 seconds)
    await wait_for_sessions_to_close()

    # Set shutdown event to signal completion
    shutdown_event.set()

    logger.info("Graceful shutdown completed")


async def broadcast_shutdown_to_sessions():
    """Broadcast shutdown info messages to all active sessions"""
    logger.info(f"Broadcasting shutdown message to {len(active_sessions)} active sessions")

    # Create list of session IDs to avoid modification during iteration
    session_ids = list(active_sessions.keys())

    for session_id in session_ids:
        session = active_sessions.get(session_id)
        if session and session.is_active:
            try:
                await session.send_info("Server is shutting down. Please reconnect when server is back online.")
                logger.info(f"Sent shutdown message to session {session_id}")
            except Exception as e:
                logger.error(f"Failed to send shutdown message to session {session_id}: {e}")

    logger.info("Shutdown broadcast completed")


async def wait_for_sessions_to_close():
    """Wait for active sessions to close gracefully"""
    if not active_sessions:
        logger.info("No active sessions to wait for")
        return

    logger.info(f"Waiting for {len(active_sessions)} sessions to close gracefully...")

    # Wait up to 10 seconds for sessions to close
    max_wait_time = 10.0
    start_time = time.time()

    while time.time() - start_time < max_wait_time:
        # Check if all sessions are closed
        active_count = sum(1 for session in active_sessions.values() if session.is_active)

        if active_count == 0:
            logger.info("All sessions closed gracefully")
            return

        # Wait 1 second before checking again
        await asyncio.sleep(1.0)
        elapsed = time.time() - start_time
        logger.info(f"Still waiting for {active_count} sessions to close ({elapsed:.1f}s/{max_wait_time}s)")

    # If we reach here, not all sessions closed gracefully
    remaining_count = sum(1 for session in active_sessions.values() if session.is_active)
    logger.warning(f"Shutdown timeout reached. {remaining_count} sessions did not close gracefully")


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
    # Schedule graceful shutdown in the event loop
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(graceful_shutdown())
    except RuntimeError:
        # No event loop running, create a new one
        asyncio.run(graceful_shutdown())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager with graceful shutdown"""
    logger.info("Starting Prompt Voice Streaming API v2")

    # Initialize settings with validation
    try:
        settings = StreamingSettings()
        logger.info("Configuration validation passed")
        logger.info(f"VAD silence: {settings.stream_vad_silence_ms}ms")
        logger.info(f"Partial interval: {settings.stream_partial_interval_ms}ms")
        logger.info(f"Max utterance: {settings.stream_max_utterance_ms}ms")
    except ValueError as e:
        logger.error(f"Configuration validation failed: {e}")
        raise

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info("Signal handlers registered for graceful shutdown")

    yield

    # Cleanup with graceful shutdown
    logger.info("Shutting down Prompt Voice Streaming API v2")

    # Initiate graceful shutdown for legacy session tracking
    await graceful_shutdown()

    # Broadcast shutdown to modular streaming sessions (/ws2)
    try:
        from backend.stream.session import registry as stream_registry
        await stream_registry.broadcast_shutdown()
    except Exception as e:
        logger.warning(f"Failed broadcasting shutdown to streaming sessions: {e}")
    # Release TTS resources
    try:
        from backend.stream.phrase_tts import release_tts_resources
        release_tts_resources()
    except Exception as e:
        logger.warning(f"Failed releasing TTS resources: {e}")

    # Clear active sessions
    active_sessions.clear()

    logger.info("Shutdown cleanup completed")


# Create FastAPI application
app = FastAPI(
    title="Prompt Voice Streaming API",
    description="Real-time voice streaming with WebSocket support",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Prompt Voice Streaming API", "phase": "Phase 4 - ASR + LLM Streaming + TTS + Latency Metrics"}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "active_sessions": len(active_sessions)}


stream_config = load_stream_config()

@app.get("/api/stream_config")
async def get_stream_config():
    return {"streaming": stream_config.as_dict()}


async def handle_websocket_messages(websocket: WebSocket, session: StreamingSession):
    """Handle incoming WebSocket messages"""
    try:
        async for message in websocket.iter_bytes():
            # Handle binary PCM data
            if isinstance(message, bytes):
                await handle_pcm_data(session, message)
            else:
                # Handle JSON control messages
                try:
                    import json
                    control_data = json.loads(message)
                    await handle_control_message(session, control_data)
                except json.JSONDecodeError:
                    await session.send_error(ErrorCode.PROTOCOL_VIOLATION, "Invalid JSON control message", recoverable=True)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session.session_id}")
        session.is_active = False
    except Exception as e:
        logger.error(f"Error in WebSocket handler for session {session.session_id}: {e}")
        await session.send_error(ErrorCode.INTERNAL, f"Connection error: {str(e)}", recoverable=False)
        session.is_active = False


async def handle_pcm_data(session: StreamingSession, pcm_data: bytes):
    """Handle incoming PCM audio data with VAD and incremental transcription"""
    try:
        # Reject oversized binary frames (>64 KB/frame)
        MAX_FRAME_SIZE = 64 * 1024  # 64 KB
        if len(pcm_data) > MAX_FRAME_SIZE:
            await session.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                f"Binary frame too large: {len(pcm_data)} bytes (max: {MAX_FRAME_SIZE})",
                recoverable=True
            )
            return

        if not session.is_active:
            return

        # Handle binary audio frames only in CAPTURING state
        if session.state != SessionState.CAPTURING:
            await session.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                f"Audio frames only accepted in CAPTURING state (current: {session.state.value})",
                recoverable=True
            )
            return

        # Check for max duration exceeded using buffer duration
        buffer_duration_ms = session._get_buffer_duration_ms()
        max_duration_ms = session.settings.stream_max_utterance_ms

        if buffer_duration_ms > max_duration_ms:
            await session.send_error(
                ErrorCode.MAX_DURATION_EXCEEDED,
                f"Maximum utterance duration ({max_duration_ms}ms) exceeded",
                recoverable=False
            )
            # Trigger finalization due to max duration
            await session._finalize_session("MAX_DURATION_EXCEEDED")
            return

        # Track t0: first audio sample received (Phase 4)
        if session.timestamps['t0_audio_start'] is None:
            session.timestamps['t0_audio_start'] = time.time()
            logger.info(f"[Latency] t0_audio_start: {session.timestamps['t0_audio_start']}")

        # Update metrics (Phase 4)
        session.metrics['audio_frames_received'] += 1
        session.metrics['total_samples_received'] += len(pcm_data) // 2  # 16-bit samples

        # Append to rolling buffer (maintains fixed size for continuous processing)
        session.rolling_buffer.extend(pcm_data)

        # Process VAD frames from the new data
        await process_vad_frames(session, pcm_data)

        # Check backpressure state (Phase 4)
        session._check_backpressure_state()

        # Check if we should process partial transcription
        if session.is_speaking:
            await session._process_partial_transcript()

            # Check if speech should end due to silence
            if session._should_end_speech():
                await session._process_speech_end()

        # Log processing info and metrics periodically (Phase 4)
        buffer_size = len(session.rolling_buffer)
        if buffer_size % (session.sample_rate * 2) < len(pcm_data):  # Roughly every second
            status = "speaking" if session.is_speaking else "silent"
            await session.send_info(f"Buffer: {buffer_size} bytes, Status: {status}")

            # Log comprehensive metrics every 10 seconds (Phase 4)
            if session.metrics['audio_frames_received'] % 100 == 0:  # ~10 seconds at 10fps
                session.log_comprehensive_metrics()

    except Exception as e:
        logger.error(f"Error handling PCM data for session {session.session_id}: {e}")
        await session.send_error(ErrorCode.INTERNAL, f"Error processing audio data: {str(e)}", recoverable=False)

async def process_vad_frames(session: StreamingSession, pcm_data: bytes):
    """Process VAD frames from PCM data"""
    try:
        # Process 30ms frames for VAD analysis
        frame_size = session.vad_frame_size * 2  # 16-bit samples
        total_frames = len(pcm_data) // frame_size

        for i in range(total_frames):
            start_idx = i * frame_size
            end_idx = start_idx + frame_size
            frame_bytes = pcm_data[start_idx:end_idx]

            if session._is_valid_vad_frame(frame_bytes):
                is_voice = session._process_vad_frame(frame_bytes)
                session._update_silence_tracking(is_voice)

    except Exception as e:
        logger.error(f"Error processing VAD frames for session {session.session_id}: {e}")


async def handle_control_message(session: StreamingSession, control_data: dict):
    """Handle incoming control messages"""
    try:
        # Validate that JSON control messages contain 'type' field
        if "type" not in control_data:
            await session.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                "Control message must contain 'type' field",
                recoverable=True
            )
            return

        message_type = control_data.get("type")

        if message_type == "start":
            await handle_start_message(session, control_data)
        elif message_type == "stop":
            await handle_stop_message(session, control_data)
        elif message_type == "cancel":
            await handle_cancel_message(session, control_data)
        else:
            await session.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                f"Unknown control message type: {message_type}",
                recoverable=True
            )

    except Exception as e:
        logger.error(f"Error handling control message for session {session.session_id}: {e}")
        await session.send_error(ErrorCode.INTERNAL, f"Error processing control message: {str(e)}", recoverable=False)


async def handle_start_message(session: StreamingSession, control_data: dict):
    """Handle session start message"""
    try:
        sample_rate = control_data.get("sample_rate", 16000)
        session.sample_rate = sample_rate
        session.is_active = True

        # Clear cancellation state if this is a reactivation
        if session.is_cancelled:
            session.is_cancelled = False
            logger.info(f"Session {session.session_id} reactivated after cancellation")

        # Transition to CAPTURING state
        if session.transition_to(SessionState.CAPTURING):
            logger.info(f"Session {session.session_id} started with sample rate {sample_rate}")
            await session.send_info(f"Session started with sample rate {sample_rate}Hz")
        else:
            await session.send_error(
                ErrorCode.PROTOCOL_VIOLATION,
                "Cannot start session in current state",
                recoverable=True
            )

    except Exception as e:
        logger.error(f"Error starting session {session.session_id}: {e}")
        await session.send_error(ErrorCode.INTERNAL, f"Failed to start session: {str(e)}", recoverable=False)


async def handle_stop_message(session: StreamingSession, control_data: dict):
    """Handle session stop message"""
    try:
        session.is_active = False

        # Process any ongoing speech segment
        if session.is_speaking:
            await session._process_speech_end()

        # Process final buffer if there's remaining audio data
        if session.rolling_buffer:
            buffer_size = len(session.rolling_buffer)
            await session.send_info(f"Processing final buffer: {buffer_size} bytes")

            # Run final transcription on remaining buffer
            final_text = session._run_incremental_transcription()
            if final_text:
                transcript_id = f"transcript_{session.session_id}_{int(time.time())}_final"
                await session.send_final_transcript(final_text, transcript_id)
            else:
                await session.send_info("Session stopped - no speech detected in final buffer")
        else:
            await session.send_info("Session stopped - no audio data to process")

        # Transition to IDLE state
        if session.transition_to(SessionState.IDLE):
            logger.info(f"Session {session.session_id} stopped")
        else:
            logger.warning(f"Session {session.session_id} stop completed but state transition failed")

    except Exception as e:
        logger.error(f"Error stopping session {session.session_id}: {e}")
        await session.send_error(ErrorCode.INTERNAL, f"Failed to stop session: {str(e)}", recoverable=False)


async def handle_cancel_message(session: StreamingSession, control_data: dict):
    """Handle session cancel message with comprehensive cleanup"""
    try:
        logger.info(f"Session {session.session_id} cancellation requested")

        # Set cancellation state but keep session active for new interactions
        session.is_cancelled = True

        # Cancel ongoing operations
        await cancel_ongoing_operations(session)

        # Clear audio buffers
        session.pcm_buffer.clear()
        session.rolling_buffer.clear()

        # Reset speech state
        session.is_speaking = False
        session.speech_start_time = 0.0
        session.silence_frames = 0
        session.total_silence_ms = 0

        # Clear transcript segments
        session.transcript_segments.clear()
        session.current_transcript_id = None

        # Reset latency tracking for new session
        session.timestamps = {
            't0_audio_start': None,
            't1_first_partial': None,
            't2_final_transcript': None,
            't3_first_llm_token': None,
            't4_first_tts_audio': None
        }

        # Transition to IDLE state
        if session.transition_to(SessionState.IDLE):
            # Log cancellation metrics
            session.log_comprehensive_metrics()
            logger.info(f"Session {session.session_id} cancelled successfully")

            # Send cancellation confirmation
            await session.send_info("Session cancelled - ready for new interaction")
        else:
            logger.warning(f"Session {session.session_id} cancellation completed but state transition failed")

    except Exception as e:
        logger.error(f"Error cancelling session {session.session_id}: {e}")
        await session.send_error(ErrorCode.INTERNAL, f"Failed to cancel session: {str(e)}", recoverable=False)


async def cancel_ongoing_operations(session: StreamingSession):
    """Cancel all ongoing operations (ASR, LLM, TTS)"""
    try:
        # Cancel ASR task (Step 3)
        if hasattr(session.session_state, 'asr_task') and session.session_state.asr_task and not session.session_state.asr_task.done():
            session.session_state.asr_task.cancel()
            try:
                await session.session_state.asr_task
            except asyncio.CancelledError:
                logger.debug(f"Session {session.session_id} - ASR task cancelled")

        # Cancel LLM streaming if in progress
        if hasattr(session.llm_service, 'cancel_current_streaming'):
            await session.llm_service.cancel_current_streaming()

        # Cancel TTS synthesis operations
        if session.tts_service:
            await cancel_tts_operations(session.tts_service)

        logger.info(f"Session {session.session_id} - ongoing operations cancelled")

    except Exception as e:
        logger.error(f"Error cancelling ongoing operations for session {session.session_id}: {e}")


async def cancel_tts_operations(tts_service: TTSStreamingService):
    """Cancel ongoing TTS operations and cleanup resources"""
    try:
        # Use the service's own cancellation method
        await tts_service.cancel_all_operations()

    except Exception as e:
        logger.error(f"Error cancelling TTS operations: {e}")


@app.websocket("/ws_legacy")
async def websocket_endpoint_legacy(websocket: WebSocket):
    """Legacy prototype WebSocket retained temporarily."""
    await websocket.accept()
    await websocket.send_json({"type":"info","message":"Legacy endpoint deprecated; migrate to /ws"})
    await websocket.close()

@app.websocket('/ws')
async def ws_primary(ws: WebSocket):
    await stream_ws_handle(ws)


@app.websocket('/ws2')
async def ws2_alias(ws: WebSocket):
    await stream_ws_handle(ws)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)