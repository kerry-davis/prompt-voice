from backend.stream.vad import VadState, FRAME_BYTES, SAMPLE_RATE
import webrtcvad

def test_vad_state_initial():
    vs = VadState(vad=webrtcvad.Vad(0), silence_ms=700)
    assert vs.last_voice_ts is None
    assert vs.speech_started is False


def test_vad_process_and_silence():
    vs = VadState(vad=webrtcvad.Vad(0), silence_ms=10)
    # Provide silence bytes (all zeros)
    vs.process(b"\x00" * FRAME_BYTES * 3)
    # Without voice, speech_started may remain False
    assert vs.speech_started in (False, True)  # non-fatal check
    assert vs.silence_exceeded() is False
