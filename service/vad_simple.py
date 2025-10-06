from __future__ import annotations
import time
from dataclasses import dataclass

FRAME_MS = 30
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
FRAME_BYTES = FRAME_SAMPLES * BYTES_PER_SAMPLE

try:
    import webrtcvad  # type: ignore
except Exception:
    webrtcvad = None

@dataclass
class SimpleVAD:
    silence_ms: int
    speech_started: bool = False
    last_voice_ts: float = 0.0

    def process(self, pcm: bytes):
        if not webrtcvad:
            return
        now = time.time()
        for i in range(0, len(pcm)-FRAME_BYTES+1, FRAME_BYTES):
            frame = pcm[i:i+FRAME_BYTES]
            if len(frame) != FRAME_BYTES:
                continue
            try:
                if webrtcvad.Vad(2).is_speech(frame, SAMPLE_RATE):
                    self.speech_started = True
                    self.last_voice_ts = now
            except Exception:
                pass

    def silence_exceeded(self) -> bool:
        if not self.speech_started:
            return False
        return (time.time() - self.last_voice_ts) * 1000 >= self.silence_ms
