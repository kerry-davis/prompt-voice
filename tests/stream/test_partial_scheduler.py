import time
from backend.stream.asr import OptionAPartialScheduler

def test_partial_scheduler_emits_on_change():
    s = OptionAPartialScheduler(interval_ms=10)
    # first update should emit
    assert s.update("hello") == "hello"
    # immediate same text should not emit
    assert s.update("hello") is None
    # wait past interval but same text still suppressed
    time.sleep(0.02)
    assert s.update("hello") is None
    # new text emits
    assert s.update("hello world") == "hello world"
