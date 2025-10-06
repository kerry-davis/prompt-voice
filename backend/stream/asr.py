"""ASR integration (C4) - faster-whisper full-buffer transcription + Option A partial hook"""
from __future__ import annotations
import time
import threading
from typing import Optional, Tuple, TYPE_CHECKING, Any
import numpy as np
from .config import load_stream_config

try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception:  # pragma: no cover
    WhisperModel = None  # type: ignore

_cfg = load_stream_config()
_model_lock = threading.Lock()
_model: Any = None
_model_load_failed: bool = False


def _canonical_model_name(name: str) -> str:
    # Normalize names like 'small-int8' -> 'small'
    if name.endswith('-int8'):
        return name.rsplit('-int8', 1)[0]
    return name

def load_model() -> Optional[Any]:
    """Lazy-load the Whisper model; mark failure so server can disable streaming."""
    global _model, _model_load_failed
    if _model is not None:
        return _model
    if WhisperModel is None:
        _model_load_failed = True
        return None
    with _model_lock:
        if _model is None and not _model_load_failed:
            try:
                model_name = _canonical_model_name(_cfg.model_whisper)
                _model = WhisperModel(model_name, device="cpu", compute_type="int8")
            except Exception:
                _model_load_failed = True
                _model = None
    return _model

def model_available() -> bool:
    if _model_load_failed:
        return False
    return load_model() is not None


def pcm_int16_bytes_to_float32(pcm: bytes) -> np.ndarray:
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    return arr


def transcribe_full(pcm: bytes) -> Tuple[str, float]:
    """Transcribe entire buffer; returns (text, decode_ms)."""
    model = load_model()
    if not model:
        return "", 0.0
    audio = pcm_int16_bytes_to_float32(pcm)
    if audio.size == 0:
        return "", 0.0
    start = time.time()
    segments, info = model.transcribe(audio, language="en")
    parts = []
    for seg in segments:
        parts.append(seg.text.strip())
    text = " ".join(parts).strip()
    return text, (time.time() - start) * 1000.0

class OptionAPartialScheduler:
    """Naive re-decode scheduler implementing Option A (full buffer re-decode)."""
    def __init__(self, interval_ms: int):
        self.interval_ms = interval_ms
        self._last_emit: float = 0.0
        self._last_text: str = ""

    def should_run(self) -> bool:
        now = time.time()
        return (now - self._last_emit) * 1000 >= self.interval_ms

    def update(self, new_text: str) -> Optional[str]:
        if new_text and new_text != self._last_text:
            self._last_text = new_text
            self._last_emit = time.time()
            return new_text
        return None
