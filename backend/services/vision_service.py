"""
vision_service.py – Lectura de imágenes del PDF con un modelo de visión local.

Usa Ollama (que internamente corre sobre llama.cpp) con el modelo qwen2.5vl:3b.
Cada imagen insertada en el PDF se describe en español y esa descripción se
guarda como un fragmento más en PostgreSQL + pgvector, para que el chat pueda
responder preguntas sobre las imágenes.

Si Ollama no está abierto o el modelo no está descargado, el PDF se sube igual
(solo se omiten las descripciones de imágenes).
"""

import os
import json
import base64
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
_backend_env = Path(__file__).resolve().parent.parent / ".env"
if _backend_env.exists():
    load_dotenv(dotenv_path=_backend_env)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:3b")
VISION_ENABLED = os.getenv("VISION_ENABLED", "true").lower() in ("1", "true", "yes", "si", "sí")
VISION_TIMEOUT = int(os.getenv("VISION_TIMEOUT", "240"))   # en CPU cada imagen puede tardar

PROMPT = (
    "Esta imagen viene de un documento PDF. Descríbela en español en 2 a 4 oraciones: "
    "qué se ve (objetos, personas, acciones, colores, escena). "
    "Si tiene texto legible, transcríbelo. Si es una gráfica, tabla o diagrama, "
    "explica qué datos o relaciones muestra. Responde solo con la descripción."
)


IMAGE_PROMPT = (
    "Describe esta imagen en español con detalle (4 a 7 oraciones) para que alguien pueda "
    "buscarla después: qué es (foto, afiche, menú, captura, documento, producto…), qué objetos, "
    "personas, lugares, colores y acciones se ven, y el ambiente (día/noche, interior/exterior). "
    "Si tiene texto legible, transcribe lo importante (títulos, nombres, precios, cifras). "
    "Responde solo con la descripción."
)


class VisionUnavailable(RuntimeError):
    pass


def is_available() -> bool:
    """True si Ollama responde y el modelo de visión está descargado."""
    if not VISION_ENABLED:
        return False
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        base = VISION_MODEL.split(":")[0]
        return any(n == VISION_MODEL or n.split(":")[0] == base for n in names)
    except Exception:
        return False


def describe_image(png_bytes: bytes, keep_alive: str = "60s", prompt: Optional[str] = None,
                   max_tokens: int = 220) -> Optional[str]:
    """Devuelve la descripción en español de la imagen, o None si falla."""
    payload = {
        "model": VISION_MODEL,
        "stream": False,
        "keep_alive": keep_alive,   # libera la RAM poco después de terminar
        "messages": [{
            "role": "user",
            "content": prompt or PROMPT,
            "images": [base64.b64encode(png_bytes).decode("ascii")],
        }],
        "options": {"temperature": 0.2, "num_predict": max_tokens},
    }
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=VISION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = ((data.get("message") or {}).get("content") or "").strip()
        return text or None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"[WARN] Ollama visión respondió {e.code}: {body[:200]}")
    except Exception as e:
        print(f"[WARN] No se pudo describir la imagen con {VISION_MODEL}: {e}")
    return None


def status() -> dict:
    return {"enabled": VISION_ENABLED, "model": VISION_MODEL, "url": OLLAMA_BASE_URL, "available": is_available()}
