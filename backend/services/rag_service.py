"""
rag_service.py – High-Performance RAG (Retrieval-Augmented Generation) pipeline.
Supports both Groq (Qwen / Llama) and Anthropic Claude with resilient token sizing and model fallback.
Optimized to strictly respect rate limits and TPM token budgets.
"""

import os
import json
import re
from typing import List, Optional, Tuple

from dotenv import load_dotenv

try:
    from services.pdf_service import query_collection
    from services import vector_store
except ImportError:
    from pdf_service import query_collection
    import vector_store

from pathlib import Path

load_dotenv()
_backend_env = Path(__file__).resolve().parent.parent / ".env"
if _backend_env.exists():
    load_dotenv(dotenv_path=_backend_env)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_WORKSPACE_ID = os.getenv("ANTHROPIC_WORKSPACE_ID", "")

# ── Prioritized Candidate Models ─────────────────────────────────────────────
# Modelos disponibles y activos en la cuenta de Groq con fallback
CANDIDATE_MODELS = [
    LLM_MODEL,
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

_groq_client = None
_anthropic_client = None


def get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        if not GROQ_API_KEY or "YOUR_KEY_HERE" in GROQ_API_KEY or GROQ_API_KEY.startswith("sk-ant-"):
            raise ValueError("GROQ_API_KEY no está configurada en backend/.env. Puedes obtener una clave gratuita en https://console.groq.com")
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None and ANTHROPIC_API_KEY and ANTHROPIC_API_KEY.startswith("sk-ant-"):
        try:
            import anthropic
            extra_headers = {}
            if ANTHROPIC_WORKSPACE_ID:
                extra_headers["anthropic-workspace-id"] = ANTHROPIC_WORKSPACE_ID
            _anthropic_client = anthropic.Anthropic(
                api_key=ANTHROPIC_API_KEY,
                default_headers=extra_headers or None,
            )
        except Exception as e:
            print(f"[WARN] No se pudo inicializar cliente de Anthropic: {e}")
            _anthropic_client = None
    return _anthropic_client


# ── Unified Call with Dynamic Fallback & Strict Token Budgeting ─────────────
def call_llm_with_fallback(messages: list, temperature: float = 0.3, max_tokens: int = 700) -> Tuple[str, str]:
    """
    Executes completion across available providers:
    1. Tries Anthropic Claude if configured and valid.
    2. Falls back to Groq with model candidates.
    Never exceeds token quotas.
    """
    # 1. Attempt Anthropic Claude if available
    anthropic_client = get_anthropic_client()
    if anthropic_client:
        for claude_model in [
            "claude-3-5-haiku-latest",
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-20241022",
            "claude-3-haiku-20240307",
            "claude-3-5-sonnet-20241022",
        ]:
            try:
                # Format system message vs user/assistant messages for Claude
                system_content = ""
                chat_msgs = []
                for m in messages:
                    if m.get("role") == "system":
                        system_content += m.get("content", "") + "\n"
                    else:
                        chat_msgs.append({"role": m.get("role"), "content": m.get("content")})

                kwargs = {
                    "model": claude_model,
                    "messages": chat_msgs,
                    "max_tokens": max_tokens,
                }
                if system_content.strip():
                    kwargs["system"] = system_content.strip()

                resp = anthropic_client.messages.create(**kwargs)
                answer = resp.content[0].text if resp.content else ""
                if answer.strip():
                    return answer, claude_model
            except Exception as e:
                print(f"[INFO] Claude '{claude_model}' no disponible ({e}). Pasando a Groq...")
                break  # If workspace issue or auth issue, jump straight to Groq

    # 2. Call Groq with strict token limit (<= 700) to avoid 1000 OTPM / 7000 ITPM Rate Limits
    client = get_groq_client()
    last_error = None
    seen = set()
    models_to_try = [m for m in CANDIDATE_MODELS if not (m in seen or seen.add(m))]

    for model_name in models_to_try:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content or ""
            if content.strip():
                return content, model_name
        except Exception as e:
            last_error = e
            print(f"[WARN] Groq model '{model_name}' falló: {e}. Probando siguiente modelo...")
            continue

    raise last_error or RuntimeError("Ningún modelo de IA respondió correctamente.")


# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres el asistente de Archivo Vivo. Respondes preguntas sobre UN archivo de la base de conocimiento (un PDF, un audio o un video) usando solo los fragmentos que se te entregan.

## Directrices:
1. Responde con información extraída de los fragmentos; si la respuesta no está, dilo claramente y no inventes.
2. Cita la ubicación tal como aparece en el contexto: [Página X] para PDF o [Minuto MM:SS] para audio y video.
3. Los fragmentos "[Imagen en la página X]" describen imágenes del PDF, los "[Fotograma MM:SS]" lo que se ve en el video y "[Imagen]" / "[Texto en la imagen]" una imagen suelta (foto, afiche, menú): úsalos para preguntas sobre lo visual. Una imagen suelta se cita como [Página 1].
4. Usa Markdown limpio (negritas, viñetas, tablas breves si aplica).
5. Responde en el idioma de la pregunta, de forma directa y profesional."""

KB_SYSTEM_PROMPT = """Eres el asistente de Archivo Vivo, una base de conocimiento con PDFs, audios y videos cortos. Respondes usando solo los fragmentos que se te entregan, que pueden venir de varios archivos.

## Directrices:
1. Responde con información de los fragmentos; si la respuesta no está, dilo claramente y no inventes.
2. Cita SIEMPRE el archivo y la ubicación con este formato exacto: [nombre_del_archivo, página X] para PDF o [nombre_del_archivo, minuto MM:SS] para audio y video.
3. Si varios archivos aportan información, combínala y cita cada uno.
4. Los fragmentos "[Imagen en la página X]" describen imágenes de un PDF, los "[Fotograma MM:SS]" lo que se ve en un video y "[Imagen]" / "[Texto en la imagen]" una imagen suelta (foto, afiche, menú), que se cita como [nombre_del_archivo, página 1].
5. Usa Markdown limpio y responde en el idioma de la pregunta."""


def fmt_time(sec) -> str:
    sec = int(round(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def location_label(chunk: dict, lower: bool = False) -> str:
    if chunk.get("start_sec") is not None:
        label = f"Minuto {fmt_time(chunk['start_sec'])}"
    else:
        label = f"Página {chunk.get('page') or '?'}"
    return label.lower() if lower else label


# ── Contexto ──────────────────────────────────────────────────────────────────
def select_relevant(chunks: List[dict], min_ratio: float = 0.6) -> List[dict]:
    """Descarta fragmentos mucho menos parecidos a la pregunta que el mejor resultado."""
    if not chunks:
        return []
    best = max(c.get("score", 0) for c in chunks)
    if best <= 0:
        return chunks[:1]
    return [c for c in chunks if c.get("score", 0) >= best * min_ratio]


def build_context(chunks: List[dict], max_chars: int = 4500, doc_index: Optional[dict] = None) -> Tuple[str, List[dict]]:
    """
    Arma el contexto con la ubicación de cada fragmento. Con doc_index (búsqueda
    en toda la base) también antepone el nombre del archivo.
    Devuelve (texto, fragmentos_incluidos).
    """
    if not chunks:
        return "No se encontró información relevante en la base de conocimiento.", []
    parts, used, total = [], [], 0
    for chunk in chunks:
        text = chunk.get("text", "").strip()
        if len(text) > 1800:
            text = text[:1800] + "..."
        if doc_index is not None:
            info = doc_index.get(chunk.get("collection_name"), {})
            entry = f"[Fuente: {info.get('name', 'archivo')} | {location_label(chunk)}]: {text}"
        else:
            entry = f"[{location_label(chunk)}]: {text}"
        if total + len(entry) > max_chars:
            if not parts:
                entry = entry[:max_chars]
            else:
                break
        parts.append(entry)
        used.append(chunk)
        total += len(entry)
    return "\n\n".join(parts), used


def _history_messages(chat_history: Optional[List[dict]]) -> List[dict]:
    out = []
    if chat_history:
        clean = [m for m in chat_history
                 if not m.get("content", "").startswith("⚠️ Error") and "Error code:" not in m.get("content", "")][-6:]
        for msg in clean:
            content = msg.get("content", "")
            out.append({"role": msg.get("role", "user"), "content": content[:400] + ("..." if len(content) > 400 else "")})
    return out


def _sources(used: List[dict], doc_index: Optional[dict] = None) -> List[dict]:
    """Fuentes únicas (archivo + página o segundo) de los fragmentos que vio el modelo."""
    seen, out = {}, []
    for c in used:
        is_media = c.get("start_sec") is not None
        key = (c.get("collection_name"), round(c["start_sec"]) if is_media else c.get("page"))
        if not is_media and not c.get("page"):
            continue
        if key in seen:                       # misma página: se agrega el fragmento para marcarlo también
            if c.get("chunk_index") is not None:
                seen[key]["chunks"].append(c["chunk_index"])
            continue
        src = {"page": None if is_media else c.get("page"), "start_sec": c.get("start_sec") if is_media else None,
               "end_sec": c.get("end_sec") if is_media else None, "kind": c.get("kind", "text"),
               "chunks": [c["chunk_index"]] if c.get("chunk_index") is not None else []}
        seen[key] = src
        if doc_index is not None:
            info = doc_index.get(c.get("collection_name"), {})
            src.update({"doc_id": info.get("id"), "name": info.get("name"), "media_type": info.get("media_type")})
        out.append(src)
    return out


CITE_RE = re.compile(
    r"\[(?:([^\[\],]+?\.[a-z0-9]{2,4}),\s*)?(p[áa]ginas?|p[áa]gs?\.?|minutos?|min\.?|fotograma)\s*([\d:,\sy–-]+?)\]",
    re.IGNORECASE)


def _parse_time(txt: str) -> Optional[float]:
    try:
        parts = [int(p) for p in txt.split(":")]
    except ValueError:
        return None
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return float(sec)


def cited_sources(answer: str, sources: List[dict], doc_index: Optional[dict] = None) -> List[dict]:
    """
    Deja solo las fuentes que la respuesta CITA ([Página 1], [archivo.pdf, página 3], [Minuto 01:20]),
    en el orden en que aparecen. Las demás fueron contexto que el modelo no usó.
    Si cita una página que no estaba en el contexto, se agrega igual (el marcado la ubica en el archivo).
    """
    by_name = {}
    if doc_index:
        for info in doc_index.values():
            by_name[(info.get("name") or "").strip().lower()] = info
    out, seen = [], set()
    for m in CITE_RE.finditer(answer or ""):
        name, unit, values = m.group(1), m.group(2).lower(), m.group(3)
        is_time = unit.startswith(("min", "foto"))
        if is_time:
            targets = [t for t in (_parse_time(v) for v in re.findall(r"\d{1,2}:\d{2}(?::\d{2})?", values)) if t is not None]
        else:
            targets = [int(v) for v in re.findall(r"\d+", values)]
        info = by_name.get(name.strip().lower()) if (name and doc_index) else None
        for t in targets:
            key = ((info or {}).get("id") or (name or "").lower(), "t" if is_time else "p", t)
            if key in seen:
                continue
            seen.add(key)
            match = None
            for s in sources:
                if name and doc_index and (s.get("name") or "").strip().lower() != name.strip().lower():
                    continue
                if is_time and s.get("start_sec") is not None:
                    end = s.get("end_sec") if s.get("end_sec") is not None else s["start_sec"]
                    if s["start_sec"] - 1.5 <= t <= end + 1.5:
                        match = s
                        break
                elif not is_time and s.get("page") == t:
                    match = s
                    break
            if match is None:
                match = {"page": None if is_time else t, "start_sec": t if is_time else None, "end_sec": None,
                         "kind": "text", "chunks": []}
                if info:
                    match.update({"doc_id": info.get("id"), "name": info.get("name"), "media_type": info.get("media_type")})
                elif doc_index:
                    continue          # cita a un archivo que no existe en la base
            if match not in out:
                out.append(match)
    return out


# ── Preguntas a un archivo ────────────────────────────────────────────────────
def ask_document(collection_name: str, user_question: str, chat_history: Optional[List[dict]] = None,
                 top_k: int = 6) -> dict:
    retrieved = select_relevant(query_collection(collection_name, user_question, top_k=top_k))
    context, used = build_context(retrieved, max_chars=4500)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *_history_messages(chat_history),
                {"role": "user", "content": f"Contexto del archivo:\n{context}\n\nPregunta del usuario:\n{user_question}"}]
    answer, used_model = call_llm_with_fallback(messages=messages, temperature=0.3, max_tokens=650)
    return {"answer": answer, "sources": cited_sources(answer, _sources(used)), "chunks_used": len(used), "model": used_model}


# ── Consulta semántica en TODA la base de conocimiento ───────────────────────
def ask_knowledge_base(user_question: str, doc_index: dict, chat_history: Optional[List[dict]] = None,
                       top_k: int = 10) -> dict:
    """
    doc_index: {collection_name: {"id", "name", "media_type"}} de los archivos de la base.
    Busca en todos los archivos a la vez (pgvector, sin filtro de colección).
    """
    retrieved = [c for c in query_collection(None, user_question, top_k=top_k) if c.get("collection_name") in doc_index]
    retrieved = select_relevant(retrieved, min_ratio=0.55)
    context, used = build_context(retrieved, max_chars=6000, doc_index=doc_index)
    messages = [{"role": "system", "content": KB_SYSTEM_PROMPT}, *_history_messages(chat_history),
                {"role": "user", "content": f"Fragmentos de la base de conocimiento:\n{context}\n\nPregunta del usuario:\n{user_question}"}]
    answer, used_model = call_llm_with_fallback(messages=messages, temperature=0.3, max_tokens=800)
    return {"answer": answer, "sources": cited_sources(answer, _sources(used, doc_index), doc_index),
            "chunks_used": len(used), "model": used_model}


# ── Resumen automático al indexar ─────────────────────────────────────────────
def generate_document_summary(collection_name: str, media_type: str = "pdf") -> dict:
    """Resumen, conceptos clave y complejidad a partir de los primeros fragmentos."""
    chunks = [c for c in vector_store.get_chunks(collection_name)][:8]
    context, _ = build_context(chunks, max_chars=4000)
    tipo = {"pdf": "un PDF", "audio": "la transcripción de un audio", "video": "un video (transcripción y fotogramas)",
            "image": "una imagen (descripción del modelo de visión y texto leído con OCR)"}.get(media_type, "un archivo")
    messages = [
        {"role": "system", "content": "Eres un asistente que resume contenidos en JSON."},
        {"role": "user", "content": f"""Analiza el siguiente contenido de {tipo} y responde ÚNICAMENTE en JSON con este formato:
{{"summary": "Resumen ejecutivo de 2 oraciones.", "key_concepts": ["concepto1", "concepto2", "concepto3"], "complexity": "Intermedio"}}

Contenido:
{context}"""},
    ]
    try:
        raw, _ = call_llm_with_fallback(messages=messages, temperature=0.2, max_tokens=400)
        raw = raw.strip()
        if "```" in raw:
            m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw)
            if m:
                raw = m.group(1)
        brace = re.search(r'\{[\s\S]*\}', raw)
        if brace:
            raw = brace.group(0)
        result = json.loads(raw)
        return {"summary": result.get("summary", "Archivo indexado correctamente."),
                "key_concepts": result.get("key_concepts", []),
                "complexity": result.get("complexity", "Intermedio")}
    except Exception as e:
        print(f"[WARN] Error en generate_document_summary: {e}")
        return {"summary": "Archivo indexado correctamente. Listo para responder tus preguntas.",
                "key_concepts": [], "complexity": "Intermedio"}
