from __future__ import annotations
import json, time, uuid, asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi import UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from .config import settings
from .asr_simple import transcribe_full, model_available
import os, functools
from .vad_simple import SimpleVAD

app = FastAPI(title="Lean Voice Service")

@app.get("/health")
async def health():
    return {
        "protocol": settings.stream_protocol_version,
        "model_loaded": model_available(),
        "model": settings.stream_model_whisper,
        "partials": settings.stream_enable_partials,
    }

@app.post("/transcribe")
async def http_transcribe(file: UploadFile = File(...)):
    if not model_available():
        raise HTTPException(status_code=503, detail="Model not loaded")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    text, ms = transcribe_full(data, settings.stream_sample_rate)
    return {"text": text, "decode_ms": ms, "bytes": len(data)}

# Static files configuration
_PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
if not _PUBLIC_DIR.exists():
    print(f"[WARN] Public directory not found at {_PUBLIC_DIR}")

# Mount assets under /static (JS, etc.)
app.mount("/static", StaticFiles(directory=str(_PUBLIC_DIR)), name="static")

@app.get("/")
async def root_index():
    index_path = _PUBLIC_DIR / "index.html"
    if not index_path.exists():
        return {"detail":"index.html missing"}
    return HTMLResponse(index_path.read_text(encoding='utf-8'))

@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    sid = str(uuid.uuid4())
    await ws.send_json({"type":"protocol","version":settings.stream_protocol_version})
    await ws.send_json({"type":"info","message":"Session created","session_id":sid})
    pcm = bytearray()
    vad = SimpleVAD(settings.stream_vad_silence_ms)
    last_audio_wall = None
    last_partial_emit = 0.0
    first_audio_ts = None
    first_partial_ts = None
    final_ts = None
    last_heartbeat = time.time()
    last_partial_text = ""
    finalize_task_started = False

    async def maybe_finalize(reason: str, upgrade: bool=False):
        nonlocal final_ts
        if final_ts is not None:
            return
        if settings.stream_debug:
            await ws.send_json({
                "type":"debug","event":"finalize_trigger","reason":reason,
                "duration_ms": len(pcm)/(settings.stream_sample_rate*2)*1000,
                "buffer_bytes": len(pcm)
            })
        await ws.send_json({"type":"finalize_pending","reason":reason})
        loop = asyncio.get_running_loop()
        buf_copy = bytes(pcm)
        if not upgrade:
            # Provisional immediate final using last partial text while full decode in background
            provisional_text = last_partial_text
            provisional_id = str(uuid.uuid4())
            await ws.send_json({"type":"final_transcript","text":provisional_text,"id":provisional_id,"provisional":True})
        text, _ = await loop.run_in_executor(None, functools.partial(transcribe_full, buf_copy, settings.stream_sample_rate))
        final_ts = time.time()
        final_id = str(uuid.uuid4())
        await ws.send_json({"type":"final_transcript","text":text,"id":final_id,"provisional":False})
        await ws.send_json({"type":"metrics","event":"latency","t0":first_audio_ts,"t1":first_partial_ts,"t2":final_ts})
        if settings.stream_save_dir:
            try:
                os.makedirs(settings.stream_save_dir, exist_ok=True)
                with open(os.path.join(settings.stream_save_dir, f"{final_id}.txt"), 'w', encoding='utf-8') as f:
                    f.write(text + "\n")
            except Exception as e:
                if settings.stream_debug:
                    await ws.send_json({"type":"debug","event":"save_error","error":str(e)})
        await ws.send_json({"type":"info","message":"Session complete"})

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=0.3)
            except asyncio.TimeoutError:
                # Evaluate inactivity / duration / VAD even without new frames
                if first_audio_ts is not None and final_ts is None:
                    duration_ms = len(pcm)/(settings.stream_sample_rate*2)*1000
                    inactivity_ms = 0 if last_audio_wall is None else (time.time()-last_audio_wall)*1000
                    if vad.silence_exceeded():
                        await maybe_finalize("vad_silence_timeout")
                        break
                    if duration_ms >= settings.stream_max_utterance_ms:
                        await maybe_finalize("max_utterance_timeout")
                        break
                    if inactivity_ms >= settings.stream_inactivity_finalize_ms:
                        await maybe_finalize("inactivity_timeout")
                        break
                # Heartbeat
                now_hb = time.time()
                if settings.stream_debug and (now_hb - last_heartbeat)*1000 >= settings.stream_debug_heartbeat_ms:
                    await ws.send_json({"type":"debug","event":"heartbeat","pcm_ms": len(pcm)/(settings.stream_sample_rate*2)*1000})
                    last_heartbeat = now_hb
                continue

            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("text") is not None:
                try:
                    data = json.loads(msg["text"])
                except Exception:
                    await ws.send_json({"type":"error","code":"BAD_JSON","recoverable":True})
                    continue
                t = data.get("type")
                if t == "start":
                    await ws.send_json({"type":"info","message":"Capture started"})
                elif t == "stop":
                    await maybe_finalize("explicit_stop")
                    break
                else:
                    await ws.send_json({"type":"error","code":"UNKNOWN_TYPE","message":t or "?","recoverable":True})
            elif msg.get("bytes") is not None:
                chunk = msg["bytes"]
                if not chunk:
                    continue
                pcm.extend(chunk)
                last_audio_wall = time.time()
                if first_audio_ts is None:
                    first_audio_ts = last_audio_wall
                vad.process(chunk)
                if settings.stream_enable_partials and model_available():
                    now = time.time()
                    if (now - last_partial_emit)*1000 >= settings.stream_partial_interval_ms and len(pcm) > 3200:
                        tail_bytes = int(settings.stream_partial_window_ms/1000*settings.stream_sample_rate*2)
                        buf = pcm[-tail_bytes:] if tail_bytes < len(pcm) else pcm
                        text, _ = transcribe_full(buf, settings.stream_sample_rate)
                        if text.strip():
                            # Basic filtering: collapse spaces, drop if too repetitive or too low alpha ratio
                            collapsed = " ".join(text.split())
                            alpha_ratio = sum(c.isalpha() for c in collapsed)/max(1,len(collapsed))
                            unique_ratio = len(set(collapsed))/max(1,len(collapsed))
                            if alpha_ratio < 0.25 or unique_ratio < 0.05:
                                if settings.stream_debug:
                                    await ws.send_json({"type":"debug","event":"partial_reject","alpha_ratio":alpha_ratio,"unique_ratio":unique_ratio})
                                collapsed = ""
                            text = collapsed
                        if text:
                            if first_partial_ts is None:
                                first_partial_ts = now
                            last_partial_text = text
                            await ws.send_json({"type":"partial_transcript","text":text})
                            last_partial_emit = now
            else:
                await ws.send_json({"type":"error","code":"BAD_FRAME","recoverable":True})
    except WebSocketDisconnect:
        pass
