import asyncio, json, argparse, wave, websockets, time, os, sys, math
from pathlib import Path

def read_wav_bytes(path: Path):
    with wave.open(str(path), 'rb') as w:
        if w.getsampwidth() != 2 or w.getframerate() != 16000 or w.getnchannels() != 1:
            raise SystemExit('WAV must be mono 16kHz 16-bit PCM')
        return w.readframes(w.getnframes())

async def stream_audio(pcm: bytes, frame_ms: int, uri: str):
    frame_bytes = int(16000 * 2 * frame_ms / 1000)
    async with websockets.connect(uri, max_size=2**23) as ws:
        await ws.send(json.dumps({'type':'start','sampling_rate':16000}))
        t_start = time.time()
        for i in range(0, len(pcm), frame_bytes):
            await ws.send(pcm[i:i+frame_bytes])
            await asyncio.sleep(frame_ms/1000.0 * 0.9)  # pace a bit faster than realtime
        await ws.send(json.dumps({'type':'stop'}))
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                print('EVENT', msg)
                if json.loads(msg).get('type') == 'final_transcript':
                    print(f'Total session wall time: {time.time()-t_start:.2f}s')
                    break
            except asyncio.TimeoutError:
                print('Timeout waiting for final transcript')
                break

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('wav', type=Path, help='Mono 16k 16-bit PCM WAV file')
    ap.add_argument('--uri', default='ws://127.0.0.1:8100/ws')
    ap.add_argument('--frame-ms', type=int, default=120)
    args = ap.parse_args()
    pcm = read_wav_bytes(args.wav)
    await stream_audio(pcm, args.frame_ms, args.uri)

if __name__ == '__main__':
    asyncio.run(main())
