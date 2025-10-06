"""LLM streaming (C7) - token generator with OpenAI or fallback"""
from __future__ import annotations
import os, time, asyncio
from typing import AsyncGenerator, List, Dict, Optional
from .config import load_stream_config

_cfg = load_stream_config()

try:
    from openai import OpenAI
    _client: Optional[OpenAI] = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None
except Exception:  # pragma: no cover
    _client = None  # type: ignore

SYSTEM_PROMPT = "You are Prompt Voice, a concise helpful voice assistant."

async def stream_tokens(messages: List[Dict[str,str]]) -> AsyncGenerator[str, None]:
    timeout_s = _cfg.llm_timeout_s
    start = time.time()
    first_token = True
    if _client:
        try:
            resp = _client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
                messages=[{"role":"system","content":SYSTEM_PROMPT}] + messages,
                stream=True,
                max_tokens=256,
                temperature=0.7,
            )
            async for chunk in resp:  # type: ignore
                if time.time() - start > timeout_s:
                    break
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception:
            # fallback to local generation below
            pass
    # Local fallback: echo last user message with embellishment word-by-word
    user_text = messages[-1]['content'] if messages else ''
    fallback = f"I heard: {user_text}. Thanks for trying streaming mode."
    for word in fallback.split():
        if time.time() - start > timeout_s:
            break
        yield word + " "
        await asyncio.sleep(0.05)
