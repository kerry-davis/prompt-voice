from backend.stream.phrase_tts import PhraseAggregator

def test_phrase_completion_punctuation():
    agg = PhraseAggregator(max_len=100)
    assert agg.add_token("Hello") is None
    assert agg.add_token(" world.") == "Hello world."


def test_phrase_force():
    agg = PhraseAggregator(max_len=100)
    agg.add_token("Partial ")
    leftover = agg.force()
    assert leftover.strip() == "Partial"
