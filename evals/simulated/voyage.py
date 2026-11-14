"""API de embeddings de Voyage simulada: vectores léxicos deterministas (bolsa de raíces)."""

import hashlib
import json
import math
import re
import unicodedata

import httpx

DIMENSIONS = 1024
_STOPWORDS = {
    "de",
    "la",
    "el",
    "en",
    "y",
    "a",
    "los",
    "las",
    "del",
    "un",
    "una",
    "por",
    "con",
    "que",
    "para",
    "es",
    "se",
    "no",
    "al",
    "lo",
    "mi",
    "me",
    "te",
    "tu",
    "su",
    "sus",
    "si",
    "o",
    "como",
    "mas",
    "pero",
    "ya",
    "le",
    "les",
    "hay",
    "son",
    "este",
    "esta",
    # Inglés (decisión 036): ninguna es una palabra del español, así que Acme no cambia.
    "the",
    "and",
    "you",
    "your",
    "my",
    "is",
    "are",
    "do",
    "does",
    "on",
    "in",
    "of",
    "to",
    "for",
    "it",
    "we",
    "be",
    "can",
    "with",
    "will",
    "at",
    "an",
    "or",
    "our",
    "this",
    "that",
    "what",
    "how",
    "when",
    "if",
    "have",
    "has",
}


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", _normalize(text))
    return [w[:5] for w in words if w not in _STOPWORDS and len(w) > 1]


def embed_text(text: str, dimensions: int = DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokens(text):
        index = int(hashlib.md5(token.encode()).hexdigest(), 16) % dimensions
        vector[index] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def voyage_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        texts: list[str] = body["input"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "embedding": embed_text(t), "index": i}
                    for i, t in enumerate(texts)
                ],
                "model": body["model"],
                "usage": {"total_tokens": sum(len(t.split()) for t in texts)},
            },
        )

    return httpx.MockTransport(handler)
