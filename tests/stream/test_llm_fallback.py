import asyncio
from backend.stream.llm_stream import stream_tokens

async def collect(gen):
    out = []
    async for t in gen:
        out.append(t)
    return out

def test_llm_fallback_stream():
    msgs = [{"role":"user","content":"testing"}]
    tokens = asyncio.run(collect(stream_tokens(msgs)))
    assert any("testing" in t for t in tokens)
    assert len(tokens) > 2
