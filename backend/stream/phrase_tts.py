"""Phrase aggregation + TTS emission (C8)"""
from __future__ import annotations
import time, base64, tempfile, os, asyncio
from dataclasses import dataclass
from typing import Optional
from .config import load_stream_config

try:
    import pyttsx3  # type: ignore
except Exception:  # pragma: no cover
    pyttsx3 = None

_cfg = load_stream_config()

@dataclass
class PhraseState:
    buf: str = ""
    last_token_ts: float = 0.0
    seq: int = 0

class PhraseAggregator:
    def __init__(self, max_len: int = 60, gap_ms: int = 700):
        self.max_len = max_len
        self.gap_ms = gap_ms
        self.state = PhraseState()

    def add_token(self, tok: str) -> Optional[str]:
        now = time.time()
        if self.state.last_token_ts and (now - self.state.last_token_ts) * 1000 >= self.gap_ms and self.state.buf:
            phrase = self.state.buf.strip()
            self.state.buf = tok
            self.state.last_token_ts = now
            return phrase
        if self.state.buf:
            self.state.buf += tok
        else:
            self.state.buf = tok
        self.state.last_token_ts = now
        if self._complete():
            phrase = self.state.buf.strip()
            self.state.buf = ""
            return phrase
        return None

    def force(self) -> Optional[str]:
        if self.state.buf.strip():
            phrase = self.state.buf.strip()
            self.state.buf = ""
            return phrase
        return None

    def next_seq(self) -> int:
        s = self.state.seq
        self.state.seq += 1
        return s

    def _complete(self) -> bool:
        if not self.state.buf:
            return False
        if self.state.buf[-1] in ".?!":
            return True
        if len(self.state.buf) >= self.max_len:
            return True
        return False

class SimpleTTS:
    def __init__(self):
        self.enabled = pyttsx3 is not None and _cfg.enabled
        self.engine = None
        if self.enabled:
            try:
                self.engine = pyttsx3.init()
                self.engine.setProperty('rate', 190)
            except Exception:
                self.enabled = False

    async def synth(self, text: str) -> Optional[bytes]:
        if not self.enabled or not text:
            return None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synth_blocking, text)

    def _synth_blocking(self, text: str) -> Optional[bytes]:
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                name = tmp.name
            self.engine.save_to_file(text, name)
            self.engine.runAndWait()
            with open(name, 'rb') as f:
                data = f.read()
            try:
                os.unlink(name)
            except Exception:
                pass
            return data
        except Exception:
            return None

    def close(self):
        try:
            if self.engine:
                # pyttsx3 has no explicit close; attempt to stop queued commands
                self.engine.stop()
        except Exception:
            pass

aggregator = PhraseAggregator()
tts = SimpleTTS()

def release_tts_resources():
    tts.close()

async def process_token(token: str, send_json):
    phrase = aggregator.add_token(token)
    if phrase:
        seq = aggregator.next_seq()
        audio = await tts.synth(phrase)
        if audio:
            await send_json({"type":"tts_chunk","seq":seq,"audio_b64":base64.b64encode(audio).decode(),"mime":"audio/wav"})
            await send_json({"type":"tts_phrase_done","seq":seq})

async def finalize_tts(send_json):
    phrase = aggregator.force()
    if phrase:
        seq = aggregator.next_seq()
        audio = await tts.synth(phrase)
        if audio:
            await send_json({"type":"tts_chunk","seq":seq,"audio_b64":base64.b64encode(audio).decode(),"mime":"audio/wav"})
            await send_json({"type":"tts_phrase_done","seq":seq})
    await send_json({"type":"tts_complete"})
