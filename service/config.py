from __future__ import annotations
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    stream_model_whisper: str = "tiny"
    stream_sample_rate: int = 16000
    stream_vad_silence_ms: int = 700
    stream_partial_interval_ms: int = 800
    # Maximum lookback window (ms) of audio to decode for partial transcripts to avoid
    # repeatedly re-decoding the full buffer which increases latency quadratically.
    stream_partial_window_ms: int = 5000
    stream_max_utterance_ms: int = 30000
    # Finalize if this many ms pass with no new audio after first audio
    stream_inactivity_finalize_ms: int = 1500
    # Debug flag to emit extra logging events over websocket
    stream_debug: bool = False
    # Optional directory to persist final transcripts (one file per session id)
    stream_save_dir: str | None = None
    # Interval for debug heartbeat (ms) when stream_debug enabled
    stream_debug_heartbeat_ms: int = 1000
    # Fast decode tuning for lean path
    stream_decode_beam_size: int = 1
    stream_decode_best_of: int = 1
    stream_protocol_version: int = 1
    stream_enable_partials: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"

settings = Settings()
