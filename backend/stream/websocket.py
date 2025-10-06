"""WebSocket skeleton (C2)"""
from __future__ import annotations
import json
import uuid
import time, json as _json
try:
    from fastapi import WebSocket, WebSocketDisconnect  # type: ignore
except Exception:  # pragma: no cover
    class WebSocketDisconnect(Exception):
        pass
    class WebSocket:  # minimal stub for smoke test environment
        async def accept(self):
            return None
        async def send_json(self, payload):
            return None
        async def receive(self):
            return {"type":"websocket.disconnect"}
        async def close(self):
            return None
from .session import StreamSession, registry
import uuid as _uuid
from .config import load_stream_config, Phase
from .vad import VadState, FRAME_BYTES, webrtcvad
from .asr import transcribe_full, OptionAPartialScheduler, model_available
from .llm_stream import stream_tokens
from .phrase_tts import process_token as tts_process_token, finalize_tts as tts_finalize
from .errors import emit_error, log_event

PROTOCOL_VERSION = 1

async def send_json_safe(ws: WebSocket, payload: dict):
    try:
        await ws.send_json(payload)
    except Exception:
        pass

async def handle(ws: WebSocket):
    await ws.accept()
    cfg = load_stream_config()
    if not cfg.enabled:
        await send_json_safe(ws, {"type":"error","code":"STREAMING_DISABLED","message":"Streaming disabled","recoverable":False})
        await ws.close()
        return
    sid = str(uuid.uuid4())
    session = StreamSession(id=sid)
    registry.add(session, ws)
    await send_json_safe(ws, {"type":"protocol","version":PROTOCOL_VERSION})
    log_event("session_open", session_id=sid)
    await send_json_safe(ws, {"type":"info","message":"Session created","session_id":sid})
    await send_json_safe(ws, {"type":"info","message":"If streaming unsupported, fall back to POST /api/voice"})
    vad_inst = None
    if webrtcvad:
        try:
            vad_inst = webrtcvad.Vad(2)
        except Exception:
            vad_inst = None
    vad_state = VadState(silence_ms=cfg.vad_silence_ms, vad=vad_inst)
    scheduler = OptionAPartialScheduler(interval_ms=cfg.partial_interval_ms)
    last_sent_partial = ""
    backpressure = False
    skip_counter = 0
    metrics = {"t0": None, "t1": None, "t2": None, "t3": None, "t4": None, "decode_runs": 0}
    try:
        while True:
            data = await ws.receive()
            if data.get('type') == 'websocket.disconnect':
                break
            if 'text' in data and data['text'] is not None:
                try:
                    msg = json.loads(data['text'])
                except json.JSONDecodeError:
                    await send_json_safe(ws, {"type":"error","code":"PROTOCOL_VIOLATION","message":"Invalid JSON","recoverable":True})
                    log_event("protocol_error", reason="invalid_json", session_id=sid)
                    continue
                mtype = msg.get('type')
                if mtype == 'start':
                    model_ok = model_available()
                    if not await session.transition(Phase.CAPTURING):
                        await send_json_safe(ws, {"type":"error","code":"PROTOCOL_VIOLATION","message":"Cannot start in phase"})
                        log_event("protocol_error", reason="bad_start_phase", phase=session.phase, session_id=sid)
                    else:
                        if not model_ok:
                            await send_json_safe(ws, {"type":"info","message":"ASR model unavailable - continuing without partial transcripts"})
                        await send_json_safe(ws, {"type":"info","message":"Capture started"})
                elif mtype == 'stop':
                    await session.transition(Phase.THINKING)
                    if metrics["t2"] is None:
                        metrics["t2"] = time.time()
                    await send_json_safe(ws, {"type":"finalize_pending"})
                    await send_json_safe(ws, {"type":"final_transcript","text":"","id":str(_uuid.uuid4())})
                    if metrics["t0"] is None:
                        metrics["t0"] = session.t0_audio_start or time.time()
                    await send_json_safe(ws, {"type":"metrics","event":"latency","t0":metrics["t0"],"t1":metrics["t1"],"t2":metrics["t2"],"t3":metrics.get("t3"),"t4":metrics.get("t4")})
                    await session.transition(Phase.COMPLETE)
                    await send_json_safe(ws, {"type":"info","message":"Session complete"})
                elif mtype == 'cancel':
                    session.cancelled = True
                    await session.transition(Phase.COMPLETE)
                    await send_json_safe(ws, {"type":"error","code":"CANCELLED","message":"Cancelled","recoverable":True})
                else:
                    await send_json_safe(ws, {"type":"error","code":"PROTOCOL_VIOLATION","message":"Unknown type","recoverable":True})
            elif 'bytes' in data and data['bytes'] is not None:
                if session.phase != Phase.CAPTURING:
                    await send_json_safe(ws, {"type":"error","code":"PROTOCOL_VIOLATION","message":"Binary before start","recoverable":False})
                    log_event("protocol_error", reason="binary_before_start", session_id=sid)
                    await ws.close()
                    break
                chunk = data['bytes']
                if len(chunk) > 64*1024:
                    await send_json_safe(ws, {"type":"error","code":"PROTOCOL_VIOLATION","message":"Frame too large","recoverable":False})
                    log_event("protocol_error", reason="frame_too_large", size=len(chunk), session_id=sid)
                    await ws.close()
                    break
                session.add_audio(chunk)
                vad_state.process(chunk)
                # attempt partial ASR if speaking started
                if vad_state.speech_started and scheduler.should_run():
                    try:
                        text, decode_ms = transcribe_full(session.pcm_bytes)
                    except Exception as e:
                        await send_json_safe(ws, {"type":"error","code":"ASR_FAIL","message":"ASR decode error","recoverable":True})
                        log_event("asr_fail", session_id=sid, error=str(e))
                        text, decode_ms = "", 0.0
                    metrics["decode_runs"] += 1
                    if metrics["t0"] is None:
                        metrics["t0"] = session.t0_audio_start or time.time()
                    # structured decode log
                    await send_json_safe(ws, {"type":"metrics","event":"decode","runs":metrics["decode_runs"],"ms":round(decode_ms,2)})
                    # backpressure heuristic: large buffer & frequent decodes
                    if len(session.pcm_bytes) > 32000 * 5 and not backpressure:  # >5s audio
                        backpressure = True
                        await send_json_safe(ws, {"type":"info","message":"Entering backpressure mode"})
                    if backpressure:
                        skip_counter += 1
                        if skip_counter % 3 != 0:
                            text = ""  # suppress emission attempt
                    if text and text != last_sent_partial:
                        if metrics["t1"] is None:
                            metrics["t1"] = time.time()
                        last_sent_partial = text
                        await send_json_safe(ws, {"type":"partial_transcript","text":text})
                    if backpressure and len(session.pcm_bytes) < 32000 * 3:
                        backpressure = False
                        skip_counter = 0
                        await send_json_safe(ws, {"type":"info","message":"Backpressure cleared"})
                # duration cap
                if session.duration_ms() > cfg.max_utterance_ms:
                    await send_json_safe(ws, {"type":"error","code":"MAX_DURATION_EXCEEDED","message":"Utterance too long","recoverable":True})
                    log_event("max_duration", duration_ms=session.duration_ms(), session_id=sid)
                    await session.transition(Phase.THINKING)
                    if metrics["t2"] is None:
                        metrics["t2"] = time.time()
                    await send_json_safe(ws, {"type":"finalize_pending"})
                    await send_json_safe(ws, {"type":"final_transcript","text":"","id":str(_uuid.uuid4())})
                    await session.transition(Phase.COMPLETE)
                    break
                # silence finalize
                if vad_state.silence_exceeded():
                    await session.transition(Phase.THINKING)
                    if metrics["t2"] is None:
                        metrics["t2"] = time.time()
                    await send_json_safe(ws, {"type":"finalize_pending"})
                    try:
                        final_text, _ = transcribe_full(session.pcm_bytes)
                    except Exception as e:
                        await send_json_safe(ws, {"type":"error","code":"ASR_FAIL","message":"ASR decode error","recoverable":True})
                        log_event("asr_fail", session_id=sid, error=str(e))
                        final_text = ""
                    if metrics["t1"] is None and final_text:
                        metrics["t1"] = time.time()  # first partial absent; treat final as first
                    await send_json_safe(ws, {"type":"final_transcript","text":final_text,"id":str(_uuid.uuid4())})
                    session.add_user_message(final_text)
                    log_event("final_transcript", length=len(final_text), session_id=sid)
                    # start LLM streaming if transcript non-empty
                    if final_text.strip():
                        await session.transition(Phase.RESPONDING)
                        user_messages = [{"role":"user","content":final_text}]
                        async for tok in stream_tokens(user_messages):
                            if metrics.get("t3") is None:
                                metrics["t3"] = time.time()
                            await send_json_safe(ws, {"type":"llm_token","text":tok,"done":False})
                            log_event("llm_token", size=len(tok), session_id=sid)
                            session.add_assistant_token(tok)
                            async def _send(m):
                                if m.get("type") == "tts_chunk" and metrics.get("t4") is None:
                                    metrics["t4"] = time.time()
                                await send_json_safe(ws, m)
                            await tts_process_token(tok, _send)
                        await send_json_safe(ws, {"type":"llm_token","done":True})
                        committed = session.commit_assistant()
                        log_event("assistant_committed", chars=len(committed), session_id=sid)
                        log_event("llm_complete", session_id=sid)
                        async def _final_send(m):
                            if m.get("type") == "tts_chunk" and metrics.get("t4") is None:
                                metrics["t4"] = time.time()
                            await send_json_safe(ws, m)
                        await tts_finalize(_final_send)
                        log_event("tts_complete", session_id=sid)
                    await send_json_safe(ws, {"type":"metrics","event":"latency","t0":metrics["t0"],"t1":metrics["t1"],"t2":metrics["t2"],"t3":metrics.get("t3"),"t4":metrics.get("t4")})
                    log_event("latency_emit", session_id=sid, t0=metrics["t0"], t1=metrics["t1"], t2=metrics["t2"], t3=metrics.get("t3"), t4=metrics.get("t4"))
                    await session.transition(Phase.COMPLETE)
                    break
            else:
                # Unknown frame type
                await send_json_safe(ws, {"type":"error","code":"PROTOCOL_VIOLATION","message":"Unsupported frame","recoverable":True})
    except WebSocketDisconnect:
        pass
    finally:
        registry.remove(sid)
        log_event("session_close", session_id=sid)
