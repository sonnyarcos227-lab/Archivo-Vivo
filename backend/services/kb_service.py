"""
kb_service.py – Base de conocimiento de Archivo Vivo.

Todo lo consultable vive en la carpeta  base_conocimiento/  (en la raíz del
proyecto). Se puede:
  • copiar archivos ahí y pulsar "Indexar carpeta" en la web  → sync_folder()
  • subirlos desde la web (quedan en base_conocimiento/subidos/) → ingest_file()

Cada archivo (PDF, audio o video) se convierte en fragmentos de texto con su
ubicación (página o segundo), se vectoriza con EmbeddingGemma 2 (llama.cpp) y se
guarda en PostgreSQL + pgvector. La base es compartida por todos los usuarios.
"""

import os
import re
import json
import uuid
import asyncio
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List

from sqlalchemy import select

try:
    from services import pdf_service, media_service, rag_service
except ImportError:
    import pdf_service, media_service, rag_service

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
KB_DIR = Path(os.getenv("KB_DIR", str(PROJECT_DIR / "base_conocimiento"))).resolve()
UPLOADS_SUBDIR = "subidos"
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "10"))
SUPPORTED_EXT = {".pdf"} | media_service.AUDIO_EXT | media_service.VIDEO_EXT | media_service.IMAGE_EXT

KB_DIR.mkdir(parents=True, exist_ok=True)
for sub in ("pdf", "audio", "video", "imagenes", UPLOADS_SUBDIR):
    (KB_DIR / sub).mkdir(exist_ok=True)

_ingest_lock = asyncio.Lock()


class IngestError(RuntimeError):
    pass


# ── Rutas ─────────────────────────────────────────────────────────────────────
def rel_path(abs_path: Path) -> str:
    return abs_path.resolve().relative_to(KB_DIR).as_posix()


def abs_path(source_path: str) -> Path:
    p = (KB_DIR / source_path).resolve()
    if KB_DIR not in p.parents and p != KB_DIR:
        raise IngestError("Ruta fuera de la base de conocimiento.")
    return p


def file_sig(p: Path) -> str:
    """Tamaño + huella de los primeros 256 KB (no usa la fecha: así no se re-indexa todo al clonar el repo)."""
    import hashlib
    with open(p, "rb") as f:
        head = f.read(256 * 1024)
    return f"{p.stat().st_size}-{hashlib.md5(head).hexdigest()[:12]}"


def doc_file_path(doc) -> Optional[str]:
    """Ruta en disco del archivo de un documento (base_conocimiento/ o uploads/ antiguo)."""
    if getattr(doc, "source_path", None):
        p = abs_path(doc.source_path)
        return str(p) if p.exists() else None
    legacy = os.path.join(pdf_service.UPLOAD_DIR, doc.filename)
    return legacy if os.path.exists(legacy) else None


def unique_destination(original_name: str) -> Path:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", original_name).strip() or "archivo"
    dest = KB_DIR / UPLOADS_SUBDIR / safe
    stem, ext = os.path.splitext(safe)
    n = 2
    while dest.exists():
        dest = KB_DIR / UPLOADS_SUBDIR / f"{stem} ({n}){ext}"
        n += 1
    return dest


def collection_for(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]", "_", os.path.splitext(name)[0])[:24]
    return f"kb_{slug}_{uuid.uuid4().hex[:10]}"


# ── Imágenes sueltas (fotos, afiches, menús, capturas) ───────────────────────
def process_image(path: str, progress=None) -> List[dict]:
    """Descripción con el modelo de visión + texto leído con OCR. Todo queda en la 'página 1'."""
    import io
    from PIL import Image, ImageOps
    vision_service = pdf_service.vision_service
    say = progress or (lambda m: None)
    try:
        im = Image.open(path)
        im.seek(0)                                  # GIF animado: primer cuadro
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception as e:
        raise IngestError(f"No se pudo abrir la imagen: {e}")
    im.thumbnail((1280, 1280))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    png = buf.getvalue()

    chunks: List[dict] = []
    say("Leyendo el texto de la imagen (OCR)…")
    engine = pdf_service.get_ocr_engine()
    if engine:
        try:
            result, _ = engine(png)
            lines = [r[1] for r in (result or []) if r and len(r) > 1 and str(r[1]).strip()]
            words = " ".join(lines).split()
            for i in range(0, len(words), 200):
                part = " ".join(words[i:i + 200])
                if len(part.split()) >= 3:
                    chunks.append({"text": f"[Texto en la imagen] {part}", "page": 1, "kind": "text"})
        except Exception as e:
            print(f"[WARN] OCR de imagen: {e}")

    if vision_service.is_available():
        say(f"Describiendo la imagen con {vision_service.VISION_MODEL}…")
        desc = vision_service.describe_image(png, keep_alive="0s", prompt=vision_service.IMAGE_PROMPT, max_tokens=400)
        if desc:
            chunks.insert(0, {"text": f"[Imagen] {desc}", "page": 1, "kind": "image"})
    else:
        print("[INFO] Modelo de visión no disponible: la imagen se indexa solo con su texto (OCR).")

    if not chunks:
        raise IngestError("No se pudo describir la imagen ni leer texto en ella. "
                          "Abre Ollama (modelo qwen2.5vl:3b) para que el modelo de visión la describa.")
    for i, c in enumerate(chunks):
        c["chunk_index"] = i
    return chunks


# ── Del archivo a fragmentos (síncrono: corre en un hilo) ────────────────────
def build_chunks(path: str, progress=None) -> Tuple[List[dict], dict]:
    say = progress or (lambda m: None)
    mtype = media_service.media_type_for(path)
    if mtype is None:
        raise IngestError(f"Formato no soportado: {os.path.basename(path)}")
    if mtype == "pdf":
        say("Leyendo el texto del PDF…")
        pages, page_count = pdf_service.extract_text_from_pdf(path)
        if page_count > MAX_PDF_PAGES:
            raise IngestError(f"El PDF tiene {page_count} páginas; el máximo es {MAX_PDF_PAGES}.")
        chunks = pdf_service.chunk_text_with_overlap(pages, chunk_size=220, overlap=40)
        for c in chunks:
            c.setdefault("kind", "text")
        say("Describiendo las imágenes del PDF con el modelo de visión…")
        chunks.extend(pdf_service.describe_pdf_images(path, len(chunks)))
        if not chunks:
            raise IngestError("No se pudo extraer texto ni imágenes del PDF.")
        return chunks, {"media_type": "pdf", "page_count": page_count, "duration_sec": None}
    if mtype == "image":
        return process_image(path, progress=say), {"media_type": "image", "page_count": 1, "duration_sec": None}
    chunks, duration, kind = media_service.process_media(path, progress=say)
    return chunks, {"media_type": kind, "page_count": 0, "duration_sec": round(duration, 2)}


def _index_sync(path: str, name: str, progress=None) -> Tuple[str, int, dict, dict]:
    """Construye fragmentos, los vectoriza y genera el resumen. Devuelve (colección, n, meta, análisis)."""
    say = progress or (lambda m: None)
    chunks, meta = build_chunks(path, progress=say)
    collection = collection_for(name)
    say(f"Generando embeddings con EmbeddingGemma 2 ({len(chunks)} fragmentos)…")
    try:
        n = pdf_service.vectorize_and_store(chunks, collection, title=name)
        say("Escribiendo el resumen…")
        analysis = rag_service.generate_document_summary(collection, meta["media_type"])
    except Exception:
        pdf_service.delete_document_data(collection)
        raise
    return collection, n, meta, analysis


async def ingest_file(db, path: Path, user_id: str, progress=None):
    """Indexa un archivo que ya está dentro de base_conocimiento/ y crea su Document."""
    from fastapi.concurrency import run_in_threadpool
    from models import Document

    path = Path(path).resolve()
    name = path.name
    async with _ingest_lock:   # un archivo a la vez: llama.cpp y Ollama usan mucha RAM
        collection, n, meta, analysis = await run_in_threadpool(_index_sync, str(path), name, progress)
    doc = Document(
        user_id=user_id, filename=rel_path(path), original_name=name,
        page_count=meta["page_count"], chunk_count=n, file_size_kb=path.stat().st_size // 1024,
        collection_name=collection, summary=analysis.get("summary"),
        key_concepts=json.dumps(analysis.get("key_concepts", []), ensure_ascii=False),
        complexity_level=analysis.get("complexity"), media_type=meta["media_type"],
        source_path=rel_path(path), duration_sec=meta["duration_sec"], file_sig=file_sig(path),
    )
    db.add(doc)
    await db.flush()
    return doc


# ── Sincronizar la carpeta ────────────────────────────────────────────────────
SYNC = {"running": False, "started_at": None, "finished_at": None, "total": 0, "done": 0,
        "current": None, "step": None, "added": [], "updated": [], "removed": [], "errors": []}


def scan_folder() -> List[Path]:
    files = []
    for p in KB_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXT and not p.name.startswith(("~", ".")):
            files.append(p.resolve())
    return sorted(files)


async def plan_sync(db):
    from models import Document
    docs = (await db.execute(select(Document))).scalars().all()
    by_path = {d.source_path: d for d in docs if d.source_path}
    files = scan_folder()
    on_disk = {rel_path(p): p for p in files}
    new = [p for r, p in on_disk.items() if r not in by_path]
    changed = [(by_path[r], p) for r, p in on_disk.items() if r in by_path and by_path[r].file_sig != file_sig(p)]
    gone = [d for r, d in by_path.items() if r not in on_disk]
    return new, changed, gone


async def sync_folder(session_factory, user_id: str):
    """Recorre base_conocimiento/: indexa lo nuevo, re-indexa lo cambiado y quita lo borrado."""
    SYNC.update(running=True, started_at=datetime.now(timezone.utc).isoformat(), finished_at=None,
                total=0, done=0, current=None, step=None, added=[], updated=[], removed=[], errors=[])
    try:
        async with session_factory() as db:
            new, changed, gone = await plan_sync(db)
            SYNC["total"] = len(new) + len(changed) + len(gone)
            for d in gone:
                SYNC.update(current=d.original_name, step="Quitando de la base (ya no está en la carpeta)")
                pdf_service.delete_document_data(d.collection_name)
                await db.delete(d)
                await db.commit()
                SYNC["removed"].append(d.original_name)
                SYNC["done"] += 1
            for item in [*[(None, p) for p in new], *changed]:
                old, p = item
                SYNC.update(current=p.name, step="Preparando…")
                try:
                    doc = await ingest_file(db, p, user_id, progress=lambda m: SYNC.update(step=m))
                    if old is not None:
                        pdf_service.delete_document_data(old.collection_name)
                        await db.delete(old)
                        SYNC["updated"].append(p.name)
                    else:
                        SYNC["added"].append(p.name)
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    traceback.print_exc()
                    SYNC["errors"].append({"file": p.name, "error": str(e)[:300]})
                SYNC["done"] += 1
    except Exception as e:
        traceback.print_exc()
        SYNC["errors"].append({"file": "(carpeta)", "error": str(e)[:300]})
    finally:
        SYNC.update(running=False, current=None, step=None, finished_at=datetime.now(timezone.utc).isoformat())


def kb_info() -> dict:
    files = scan_folder()
    counts = {"pdf": 0, "audio": 0, "video": 0, "image": 0}
    for p in files:
        counts[media_service.media_type_for(p.name)] += 1
    return {"path": str(KB_DIR), "files_on_disk": len(files), "by_type": counts}
