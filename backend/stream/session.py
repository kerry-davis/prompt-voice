"""Session management skeleton (C2)"""
from __future__ import annotations
import time
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any
from .config import Phase, load_stream_config

class SessionRegistry:
    def __init__(self):
        self._sessions: Dict[str, 'StreamSession'] = {}
        self._websockets: Dict[str, any] = {}
    def add(self, s: 'StreamSession', ws: any = None):
        self._sessions[s.id] = s
        if ws is not None:
            self._websockets[s.id] = ws
    def attach_ws(self, sid: str, ws: any):
        if sid in self._sessions:
            self._websockets[sid] = ws
    def remove(self, sid: str):
        self._sessions.pop(sid, None)
        self._websockets.pop(sid, None)
    def get(self, sid: str) -> Optional['StreamSession']:
        return self._sessions.get(sid)
    def all(self):
        return list(self._sessions.values())
    def websockets(self):
        return list(self._websockets.items())

    async def broadcast_shutdown(self, reason: str = "server_shutdown"):
        # send info then close each websocket
        for sid, ws in list(self._websockets.items()):
            try:
                await ws.send_json({"type":"info","message":"Server shutting down","reason":reason})
                await ws.close()
            except Exception:
                pass

registry = SessionRegistry()

@dataclass
class StreamSession:
    id: str
    phase: Phase = Phase.IDLE
    created_at: float = field(default_factory=time.time)
    pcm_bytes: bytearray = field(default_factory=bytearray)
    t0_audio_start: Optional[float] = None
    last_partial_time: Optional[float] = None
    last_partial_text: str = ""
    cancelled: bool = False
    config = load_stream_config()
    # Conversation memory gating
    messages: list = field(default_factory=list)  # committed messages
    _pending_assistant: list[str] = field(default_factory=list)  # accumulating tokens
    last_final_transcript: Optional[str] = None

    def add_user_message(self, text: str):
        if text.strip():
            self.messages.append({"role":"user","content":text})
            self.last_final_transcript = text

    def add_assistant_token(self, tok: str):
        if tok:
            self._pending_assistant.append(tok)

    def commit_assistant(self):
        if self._pending_assistant:
            full = ''.join(self._pending_assistant).strip()
            if full:
                self.messages.append({"role":"assistant","content":full})
            self._pending_assistant.clear()
            return full
        return ""

    async def transition(self, target: Phase) -> bool:
        valid = {
            Phase.IDLE: {Phase.CAPTURING},
            Phase.CAPTURING: {Phase.THINKING},
            Phase.THINKING: {Phase.RESPONDING, Phase.COMPLETE},
            Phase.RESPONDING: {Phase.COMPLETE},
            Phase.COMPLETE: set(),
            Phase.ERROR: set(),
        }
        if target in valid.get(self.phase, set()):
            self.phase = target
            return True
        return False

    def add_audio(self, chunk: bytes):
        self.pcm_bytes.extend(chunk)
        if self.t0_audio_start is None:
            self.t0_audio_start = time.time()

    def duration_ms(self) -> float:
        # 16kHz * 2 bytes
        return (len(self.pcm_bytes) / 32000.0) * 1000.0
