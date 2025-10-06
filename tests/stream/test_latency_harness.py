import uuid
from fastapi.testclient import TestClient
from app import app

def test_stream_latency_basic():
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type":"start","sample_rate":16000})
        ws.send_bytes(b"\x00" * 3200)
        ws.send_json({"type":"stop"})
        final_id = None
        got_latency = False
        for _ in range(30):
            msg = ws.receive_json()
            t = msg.get("type")
            if t == "final_transcript":
                final_id = msg.get("id")
            if t == "metrics" and msg.get("event") == "latency":
                assert {"t0","t1","t2","t3","t4"}.issubset(msg.keys())
                got_latency = True
            if t == "info" and msg.get("message") == "Session complete":
                break
        assert final_id, "final_transcript id missing"
        assert got_latency, "latency metrics event missing"
