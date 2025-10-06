"""Error taxonomy & structured logging helpers (C9)"""
from __future__ import annotations
import time, json, logging
from typing import Any, Dict

logger = logging.getLogger("promptvoice.stream")

FATAL_CLOSE = {"PROTOCOL_VIOLATION","INTERNAL","MODEL_LOAD_FAIL"}
RECOVERABLE = {"ASR_FAIL","ASR_TIMEOUT","LLM_TIMEOUT","TTS_FAIL","TTS_TIMEOUT","BACKPRESSURE","MAX_DURATION_EXCEEDED","CANCELLED","UNSUPPORTED_SAMPLE_RATE"}

ALL_CODES = FATAL_CLOSE | RECOVERABLE

def log_event(event: str, **fields: Any) -> None:
    payload = {"ts": time.time(), "event": event, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False))

async def emit_error(send_json, code: str, message: str, recoverable: bool | None = None):
    if recoverable is None:
        recoverable = code in RECOVERABLE
    await send_json({"type":"error","code":code,"message":message,"recoverable":recoverable})
    log_event("error", code=code, recoverable=recoverable, message=message)
