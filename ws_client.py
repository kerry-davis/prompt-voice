import asyncio, json, base64, numpy as np, websockets, time, sys

sr = 16000
# Simple synthetic waveform (amplitude-modulated noise bursts)
rng = np.random.default_rng(0)
segments = []
for _ in range(6):
    dur = 0.25
    samples = int(dur * sr)
    noise = rng.normal(0, 0.18, samples).astype(np.float32)
    env = np.linspace(0, 1, samples, dtype=np.float32)
    segments.append(noise * env)
    segments.append(np.zeros(int(0.05 * sr), dtype=np.float32))
wave = np.concatenate(segments)

async def run():
    uri = 'ws://127.0.0.1:8100/ws'
    print('Connecting to', uri)
    async with websockets.connect(uri, max_size=2**23) as ws:
        await ws.send(json.dumps({'type':'start','sampling_rate':sr}))
        frame_len = int(0.12 * sr)
        for i in range(0, len(wave), frame_len):
            chunk = wave[i:i+frame_len]
            pcm = (chunk * 32767).clip(-32768,32767).astype('<i2').tobytes()
            await ws.send(pcm)  # binary frame
            await asyncio.sleep(0.02)
        await ws.send(json.dumps({'type':'stop'}))
        # Collect events until final_transcript
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                print('EVENT', msg)
                try:
                    if json.loads(msg).get('type') == 'final_transcript':
                        break
                except Exception:
                    pass
            except asyncio.TimeoutError:
                print('Timeout waiting for final transcript')
                break

if __name__ == '__main__':
    asyncio.run(run())
