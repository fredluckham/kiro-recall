"""Ollama embedding helper — async wrapper around local API."""

import httpx

OLLAMA_URL = "http://localhost:11434/api/embed"
MODEL = "qwen3-embedding:0.6b"


async def embed(text: str) -> list[float] | None:
    """Embed a single text string. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(OLLAMA_URL, json={"model": MODEL, "input": text})
            resp.raise_for_status()
            data = resp.json()
            return data["embeddings"][0]
    except Exception:
        return None


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed multiple texts. Returns list aligned with input."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(OLLAMA_URL, json={"model": MODEL, "input": texts})
            resp.raise_for_status()
            data = resp.json()
            return data["embeddings"]
    except Exception:
        return [None] * len(texts)
