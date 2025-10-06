"""VAD & duration guards (C3)"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional
try:
    import webrtcvad  # type: ignore
except Exception:  # pragma: no cover
    webrtcvad = None  # type: ignore

FRAME_MS = 30
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
FRAME_BYTES = FRAME_SAMPLES * BYTES_PER_SAMPLE

@dataclass
class VadState:
    vad: object
    silence_ms: int
    last_voice_ts: Optional[float] = None
    speech_started: bool = False

    def process(self, pcm_chunk: bytes) -> None:
        now = time.time()
        if not self.vad:
            return
        for i in range(0, len(pcm_chunk) - FRAME_BYTES + 1, FRAME_BYTES):
            frame = pcm_chunk[i:i+FRAME_BYTES]
            if len(frame) != FRAME_BYTES:
                continue
            is_voice = False
            try:
                is_voice = self.vad.is_speech(frame, SAMPLE_RATE)
            except Exception:
                pass
            if is_voice:
                self.last_voice_ts = now
                if not self.speech_started:
                    self.speech_started = True

    def silence_exceeded(self) -> bool:
        if not self.speech_started or self.last_voice_ts is None:
            return False
        return (time.time() - self.last_voice_ts) * 1000 >= self.silence_ms

