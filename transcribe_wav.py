#!/usr/bin/env python
import sys, wave, numpy as np, json
from service.asr_simple import transcribe_full, model_available
from service.config import settings

def read_wav(path):
    with wave.open(path, 'rb') as w:
        if w.getsampwidth()!=2 or w.getframerate()!=settings.stream_sample_rate or w.getnchannels()!=1:
            raise SystemExit('WAV must be mono 16kHz 16-bit PCM')
        return w.readframes(w.getnframes())

def main():
    if len(sys.argv)<2:
        print('Usage: transcribe_wav.py file.wav'); return
    pcm = read_wav(sys.argv[1])
    text, ms = transcribe_full(pcm, settings.stream_sample_rate)
    out = {"file": sys.argv[1], "text": text, "decode_ms": ms, "model": settings.stream_model_whisper, "model_loaded": model_available()}
    print(json.dumps(out, indent=2))

if __name__ == '__main__':
    main()
