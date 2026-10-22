"""
embedding_service.py – Embeddings con EmbeddingGemma 2 servido por llama.cpp.

llama.cpp corre como servidor local (llama-server) con la API compatible con
OpenAI en /v1/embeddings. run.bat lo arranca así:

    llama-server -hf unsloth/embeddinggemma-2-GGUF:UD-Q4_K_XL --embeddings --port 8081

EmbeddingGemma 2 (Google, octubre 2026) produce vectores de 768 dimensiones y
pide prefijos distintos para documentos y para preguntas:
    documento:  "title: {titulo} | text: {contenido}"
    pregunta:   "task: search result | query: {pregunta}"
"""

import os
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()
_backend_env = Path(__file__).resolve().parent.parent / ".env"
if _backend_env.exists():
    load_dotenv(dotenv_path=_backend_env)

LLAMACPP_EMBED_URL = os.getenv("LLAMACPP_EMBED_URL", "http://127.0.0.1:8081").rstrip("/")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "EmbeddingGemma 2 (llama.cpp)")
EMBED_MODEL_HF = os.getenv("EMBED_MODEL_HF", "unsloth/embeddinggemma-2-GGUF:UD-Q4_K_XL")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))
EMBED_BATCH = int(os.getenv("EMBED_BATCH", "8"))
EMBED_TIMEOUT = int(os.getenv("EMBED_TIMEOUT", "180"))
MAX_CHARS = 6000  # recorte de seguridad por fragmento (~1.500 tokens)

START_HINT = (
    "llama.cpp no está corriendo. Ábrelo con:  llama-server -hf "
    f"{EMBED_MODEL_HF} --embeddings --port 8081   (run.bat lo hace solo)"
)


class EmbeddingUnavailable(RuntimeError):
    pass


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{LLAMACPP_EMBED_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise EmbeddingUnavailable(f"llama.cpp respondió {e.code}: {body[:300]}") from e
    except urllib.error.URLError as e:
        raise EmbeddingUnavailable(START_HINT) from e


def _embed(texts: List[str]) -> List[List[float]]:
    vectors: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [t[:MAX_CHARS] for t in texts[i:i + EMBED_BATCH]]
        data = _post("/v1/embeddings", {"model": "embeddinggemma2", "input": batch, "encoding_format": "float"})
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        if len(items) != len(batch):
            raise EmbeddingUnavailable("llama.cpp devolvió una cantidad de vectores distinta a la pedida.")
        for it in items:
            vec = it["embedding"]
            if vec and isinstance(vec[0], list):   # algunas versiones devuelven [[...]]
                vec = vec[0]
            vectors.append([float(x) for x in vec])
    if vectors and len(vectors[0]) != EMBED_DIM:
        raise EmbeddingUnavailable(
            f"El modelo devolvió vectores de {len(vectors[0])} dimensiones y se esperaban {EMBED_DIM}. "
            "Revisa que llama-server esté usando EmbeddingGemma 2."
        )
    return vectors


def embed_documents(texts: List[str], title: Optional[str] = None) -> List[List[float]]:
    """Vectores para fragmentos de la base de conocimiento."""
    t = (title or "none").replace("|", " ").strip() or "none"
    return _embed([f"title: {t} | text: {x}" for x in texts])


def embed_query(question: str) -> List[float]:
    """Vector para una pregunta del usuario."""
    return _embed([f"task: search result | query: {question}"])[0]


def status() -> dict:
    try:
        with urllib.request.urlopen(f"{LLAMACPP_EMBED_URL}/health", timeout=3) as resp:
            ok = resp.status == 200
        return {"online": ok, "engine": "llama.cpp (llama-server)", "model": EMBED_MODEL_NAME,
                "hf": EMBED_MODEL_HF, "dim": EMBED_DIM, "url": LLAMACPP_EMBED_URL}
    except Exception as e:
        return {"online": False, "engine": "llama.cpp (llama-server)", "model": EMBED_MODEL_NAME,
                "url": LLAMACPP_EMBED_URL, "hint": START_HINT, "error": str(e)[:200]}
