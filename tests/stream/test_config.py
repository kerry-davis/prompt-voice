import os
from backend.stream.config import load_stream_config

def test_config_defaults():
    cfg = load_stream_config()
    assert 300 <= cfg.vad_silence_ms <= 2000
    assert 250 <= cfg.partial_interval_ms <= 3000
    assert cfg.max_utterance_ms <= 120000
    assert cfg.enabled is True
