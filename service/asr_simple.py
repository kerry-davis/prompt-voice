from __future__ import annotations
import time
from typing import Optional, Tuple
from faster_whisper import WhisperModel
from .config import settings

_model: Optional[WhisperModel] = None
_model_failed: bool = False


def _canonical(name: str) -> str:
    return name[:-5] if name.endswith('-int8') else name

def load_model() -> Optional[WhisperModel]:
    global _model, _model_failed
    if _model or _model_failed:
        return _model
    try:
        _model = WhisperModel(_canonical(settings.stream_model_whisper), device="cpu", compute_type="int8")
    except Exception:
        _model_failed = True
        _model = None
    return _model

def model_available() -> bool:
    return load_model() is not None

def transcribe_full(pcm: bytes, sample_rate: int, *, provisional: bool=False) -> Tuple[str, float]:
    m = load_model()
    if not m or not pcm:
        return "", 0.0
    import numpy as np
    arr = (np.frombuffer(pcm, dtype=np.int16).astype('float32') / 32768.0)
    start = time.time()
    segments, info = m.transcribe(
        arr,
        language="en",
        beam_size=settings.stream_decode_beam_size,
        best_of=settings.stream_decode_best_of,
        temperature=0.0,
        vad_filter=False,
    )
    text_parts = [seg.text.strip() for seg in segments]
    return " ".join(p for p in text_parts if p).strip(), (time.time()-start)*1000.0
