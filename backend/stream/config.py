"""Streaming configuration and enums (C1)"""
from __future__ import annotations
import os
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

class Phase(str, Enum):
    IDLE = "IDLE"
    CAPTURING = "CAPTURING"
    THINKING = "THINKING"
    RESPONDING = "RESPONDING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"

@dataclass
class StreamConfig:
    model_whisper: str = os.getenv("STREAM_MODEL_WHISPER", "small-int8")
    vad_silence_ms: int = int(os.getenv("STREAM_VAD_SILENCE_MS", "700"))
    partial_interval_ms: int = int(os.getenv("STREAM_PARTIAL_INTERVAL_MS", "1000"))
    max_utterance_ms: int = int(os.getenv("STREAM_MAX_UTTERANCE_MS", "30000"))
    max_pending_phrases: int = int(os.getenv("STREAM_MAX_PENDING_PHRASES", "5"))
    log_latency: bool = os.getenv("STREAM_LOG_LATENCY", "1") == "1"
    llm_timeout_s: int = int(os.getenv("STREAM_LLM_TIMEOUT_S", "30"))
    tts_timeout_s: int = int(os.getenv("STREAM_TTS_TIMEOUT_S", "10"))
    enabled: bool = os.getenv("STREAMING_ENABLED", "1") == "1"

    def validate(self) -> None:
        errs = []
        if not (300 <= self.vad_silence_ms <= 2000):
            errs.append(f"vad_silence_ms must be 300-2000 (got {self.vad_silence_ms})")
        if not (250 <= self.partial_interval_ms <= 3000):
            errs.append(f"partial_interval_ms must be 250-3000 (got {self.partial_interval_ms})")
        if self.max_utterance_ms > 120000:
            errs.append(f"max_utterance_ms must be <=120000 (got {self.max_utterance_ms})")
        if self.max_pending_phrases < 1:
            errs.append("max_pending_phrases must be >=1")
        if self.llm_timeout_s < 5:
            errs.append("llm_timeout_s must be >=5")
        if self.tts_timeout_s < 1:
            errs.append("tts_timeout_s must be >=1")
        if errs:
            raise ValueError("Configuration validation failed:\n" + "\n".join(errs))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model_whisper": self.model_whisper,
            "vad_silence_ms": self.vad_silence_ms,
            "partial_interval_ms": self.partial_interval_ms,
            "max_utterance_ms": self.max_utterance_ms,
            "max_pending_phrases": self.max_pending_phrases,
            "log_latency": self.log_latency,
            "llm_timeout_s": self.llm_timeout_s,
            "tts_timeout_s": self.tts_timeout_s,
            "enabled": self.enabled,
        }

def load_stream_config() -> StreamConfig:
    cfg = StreamConfig()
    cfg.validate()
    return cfg
