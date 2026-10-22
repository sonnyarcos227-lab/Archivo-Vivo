"""
main.py – API de Archivo Vivo (FastAPI).

Base de conocimiento compartida con PDFs, audios y videos cortos:
  • /api/pdf/...  archivos de la base (se conservan las rutas originales del ChatPDF)
  • /api/chat/... preguntas a UN archivo
  • /api/kb/...   consulta semántica en TODA la base e indexación de la carpeta
"""

import sys
import os
import json
import asyncio
import mimetypes
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, List, Any

_CURRENT_DIR = str(Path(__file__).resolve().parent)
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

load_dotenv()

from database import init_db, get_db, async_session_factory
from models import User, Document, ChatMessage, KBMessage
from auth import router as auth_router, get_current_user, seed_default_admin
from admin import router as admin_router
from services.pdf_service import delete_document_data
from services.rag_service import ask_document, ask_knowledge_base, cited_sources
from services import vector_store, vision_service, embedding_service, media_service, kb_service, highlight_service

APP_NAME = "Archivo Vivo"
MAX_PDF_PAGES = kb_service.MAX_PDF_PAGES
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    print("[OK] Database initialized")
    async with async_session_factory() as session:
        try:
            await seed_default_admin(session)
            await session.commit()
        except Exception as e:
            print(f"[WARN] Error seeding admin: {e}")
            await session.rollback()
    print(f"[OK] Base de conocimiento: {kb_service.KB_DIR}")
    # el motor de OCR tarda en cargar: se prepara en segundo plano para que el primer marcado sea rápido
    asyncio.get_running_loop().run_in_executor(None, kb_service.pdf_service.get_ocr_engine)
    yield
    print("[INFO] Shutting down")


app = FastAPI(title=f"{APP_NAME} API", description="Base de conocimiento con PDFs, imágenes, audios y videos y consulta semántica",
              version="2.0.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": f"Error del servidor: {str(exc)}"})


app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(admin_router)


# ── Esquemas ──────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    document_id: str
    question: str


class KBAskRequest(BaseModel):
    question: str


class HighlightRequest(BaseModel):
    chunks: List[int] = []
    page: Optional[int] = None
    start_sec: Optional[float] = None
    text: str = ""
    question: str = ""
    answer: str = ""


class ChatResponse(BaseModel):
    answer: str
    sources: List[Any]
    chunks_used: int
    model: str


class DocumentOut(BaseModel):
    id: str
    original_name: str
    page_count: int
    chunk_count: int
    file_size_kb: int
    uploaded_at: str
    summary: Optional[str]
    key_concepts: Optional[List[str]]
    complexity_level: Optional[str]
    media_type: str = "pdf"
    duration_sec: Optional[float] = None
    source_path: Optional[str] = None
    uploaded_by: Optional[str] = None
    can_delete: bool = False


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    sources: Optional[List[Any]]
    created_at: str


def to_out(doc: Document, user: Optional[User] = None, owner_name: Optional[str] = None) -> DocumentOut:
    try:
        kc = json.loads(doc.key_concepts) if doc.key_concepts else []
    except (json.JSONDecodeError, TypeError):
        kc = []
    return DocumentOut(
        id=doc.id, original_name=doc.original_name, page_count=doc.page_count or 0, chunk_count=doc.chunk_count or 0,
        file_size_kb=doc.file_size_kb or 0, uploaded_at=doc.uploaded_at.isoformat(), summary=doc.summary,
        key_concepts=kc, complexity_level=doc.complexity_level, media_type=doc.media_type or "pdf",
        duration_sec=doc.duration_sec, source_path=doc.source_path, uploaded_by=owner_name,
        can_delete=bool(user and (user.id == doc.user_id or user.role == "admin")),
    )


async def _get_document(db: AsyncSession, document_id: str) -> Document:
    doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return doc


# ── Subir un archivo (PDF, audio o video) ────────────────────────────────────
@app.post("/api/pdf/upload", response_model=DocumentOut)
async def upload_file(file: UploadFile = File(...), db: AsyncSession = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    mtype = media_service.media_type_for(file.filename or "")
    if mtype is None:
        raise HTTPException(status_code=400, detail="Formato no soportado. Sube un PDF, una imagen (jpg, png, webp…), un audio (mp3, wav, m4a…) o un video (mp4, mov, webm…).")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"El archivo supera el límite de {MAX_UPLOAD_MB} MB.")
    dest = kb_service.unique_destination(file.filename)
    dest.write_bytes(data)
    try:
        doc = await kb_service.ingest_file(db, dest, current_user.id)
    except (kb_service.IngestError, media_service.MediaError) as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))
    except (embedding_service.EmbeddingUnavailable, vector_store.VectorDBUnavailable) as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Error procesando el archivo: {e}")
    return to_out(doc, current_user, current_user.full_name or current_user.username)


# ── Listar la base de conocimiento (compartida) ──────────────────────────────
@app.get("/api/pdf/documents", response_model=List[DocumentOut])
async def list_documents(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = (await db.execute(select(Document, User).join(User, User.id == Document.user_id, isouter=True)
                             .order_by(desc(Document.uploaded_at)))).all()
    return [to_out(d, current_user, (u.full_name or u.username) if u else None) for d, u in rows]


# ── Archivo original (visor PDF / reproductor de audio y video) ──────────────
@app.get("/api/pdf/documents/{document_id}/file")
async def get_document_file(document_id: str, db: AsyncSession = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    doc = await _get_document(db, document_id)
    path = kb_service.doc_file_path(doc)
    if not path:
        raise HTTPException(status_code=404, detail="El archivo ya no existe en la base de conocimiento")
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=doc.original_name)


# ── Fragmentos indexados (Explorador de Fragmentos / transcripción) ──────────
@app.get("/api/pdf/documents/{document_id}/chunks")
async def get_document_chunks(document_id: str, db: AsyncSession = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    doc = await _get_document(db, document_id)
    chunks = await run_in_threadpool(vector_store.get_chunks, doc.collection_name)
    return [{"index": i + 1, "chunk_index": c["chunk_index"], "page": c["page"], "start_sec": c["start_sec"], "end_sec": c["end_sec"],
             "kind": c["kind"], "words": len(c["text"].split()), "dims": c["dims"], "text": c["text"]}
            for i, c in enumerate(chunks)]


# ── Chunks de marcado: dónde está, en el archivo, lo que usó la respuesta ────
@app.post("/api/pdf/documents/{document_id}/highlight")
async def highlight_document(document_id: str, body: HighlightRequest, db: AsyncSession = Depends(get_db),
                             current_user: User = Depends(get_current_user)):
    doc = await _get_document(db, document_id)
    try:
        return await run_in_threadpool(highlight_service.highlight, doc, kb_service.doc_file_path(doc),
                                       body.chunks, body.page, body.text[:4000], body.start_sec,
                                       body.question[:1000], body.answer[:4000])
    except vector_store.VectorDBUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── Eliminar (quien lo subió o un administrador) ─────────────────────────────
@app.delete("/api/pdf/documents/{document_id}")
async def delete_document(document_id: str, db: AsyncSession = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    doc = await _get_document(db, document_id)
    if doc.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Solo quien subió el archivo o un administrador puede eliminarlo")
    delete_document_data(doc.collection_name, file_path=kb_service.doc_file_path(doc))
    await db.delete(doc)
    return {"detail": "Archivo eliminado de la base de conocimiento"}


# ── Preguntas a UN archivo ────────────────────────────────────────────────────
@app.post("/api/chat/ask", response_model=ChatResponse)
async def chat_ask(body: ChatRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = await _get_document(db, body.document_id)
    history = (await db.execute(select(ChatMessage).where(ChatMessage.document_id == doc.id, ChatMessage.user_id == current_user.id)
                                .order_by(ChatMessage.created_at))).scalars().all()
    try:
        result = await run_in_threadpool(ask_document, collection_name=doc.collection_name, user_question=body.question,
                                         chat_history=[{"role": m.role, "content": m.content} for m in history], top_k=6)
    except (embedding_service.EmbeddingUnavailable, vector_store.VectorDBUnavailable) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el modelo de IA: {e}")
    db.add(ChatMessage(user_id=current_user.id, document_id=doc.id, role="user", content=body.question))
    db.add(ChatMessage(user_id=current_user.id, document_id=doc.id, role="assistant", content=result["answer"],
                       sources=json.dumps(result["sources"], ensure_ascii=False)))
    await db.flush()
    return ChatResponse(**result)


def _messages_out(messages) -> List[MessageOut]:
    out = []
    for m in messages:
        try:
            sources = json.loads(m.sources) if m.sources else None
        except (json.JSONDecodeError, TypeError):
            sources = None
        if m.role == "assistant" and sources and all(isinstance(s, dict) for s in sources):
            # historial guardado antes del filtro: deja solo las fuentes que la respuesta cita
            idx = {s.get("doc_id") or str(i): {"id": s.get("doc_id"), "name": s.get("name"), "media_type": s.get("media_type")}
                   for i, s in enumerate(sources)} if any(s.get("name") for s in sources) else None
            sources = cited_sources(m.content, sources, idx)
        out.append(MessageOut(id=m.id, role=m.role, content=m.content, sources=sources, created_at=m.created_at.isoformat()))
    return out


@app.get("/api/chat/history/{document_id}", response_model=List[MessageOut])
async def chat_history(document_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    msgs = (await db.execute(select(ChatMessage).where(ChatMessage.document_id == document_id, ChatMessage.user_id == current_user.id)
                             .order_by(ChatMessage.created_at))).scalars().all()
    return _messages_out(msgs)


@app.delete("/api/chat/history/{document_id}")
async def clear_chat_history(document_id: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    msgs = (await db.execute(select(ChatMessage).where(ChatMessage.document_id == document_id, ChatMessage.user_id == current_user.id))).scalars().all()
    for m in msgs:
        await db.delete(m)
    return {"detail": "Historial eliminado"}


# ── Consulta semántica en TODA la base ───────────────────────────────────────
@app.post("/api/kb/ask", response_model=ChatResponse)
async def kb_ask(body: KBAskRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    docs = (await db.execute(select(Document))).scalars().all()
    if not docs:
        raise HTTPException(status_code=400, detail="La base de conocimiento está vacía. Sube archivos o indexa la carpeta.")
    doc_index = {d.collection_name: {"id": d.id, "name": d.original_name, "media_type": d.media_type or "pdf"} for d in docs}
    history = (await db.execute(select(KBMessage).where(KBMessage.user_id == current_user.id).order_by(KBMessage.created_at))).scalars().all()
    try:
        result = await run_in_threadpool(ask_knowledge_base, body.question, doc_index,
                                         [{"role": m.role, "content": m.content} for m in history])
    except (embedding_service.EmbeddingUnavailable, vector_store.VectorDBUnavailable) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el modelo de IA: {e}")
    db.add(KBMessage(user_id=current_user.id, role="user", content=body.question))
    db.add(KBMessage(user_id=current_user.id, role="assistant", content=result["answer"],
                     sources=json.dumps(result["sources"], ensure_ascii=False)))
    await db.flush()
    return ChatResponse(**result)


@app.get("/api/kb/history", response_model=List[MessageOut])
async def kb_history(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    msgs = (await db.execute(select(KBMessage).where(KBMessage.user_id == current_user.id).order_by(KBMessage.created_at))).scalars().all()
    return _messages_out(msgs)


@app.delete("/api/kb/history")
async def kb_clear_history(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    for m in (await db.execute(select(KBMessage).where(KBMessage.user_id == current_user.id))).scalars().all():
        await db.delete(m)
    return {"detail": "Historial eliminado"}


# ── Indexar la carpeta base_conocimiento/ ────────────────────────────────────
_sync_task: Optional[asyncio.Task] = None


@app.post("/api/kb/sync")
async def kb_sync(current_user: User = Depends(get_current_user)):
    global _sync_task
    if kb_service.SYNC["running"]:
        return {"detail": "Ya se está indexando la carpeta", "status": kb_service.SYNC}
    async with async_session_factory() as db:
        new, changed, gone = await kb_service.plan_sync(db)
    _sync_task = asyncio.create_task(kb_service.sync_folder(async_session_factory, current_user.id))
    return {"detail": "Indexación iniciada", "pending": {"nuevos": len(new), "cambiados": len(changed), "eliminados": len(gone)}}


@app.get("/api/kb/status")
async def kb_status(current_user: User = Depends(get_current_user)):
    async with async_session_factory() as db:
        new, changed, gone = await kb_service.plan_sync(db)
    return {**kb_service.kb_info(), "sync": kb_service.SYNC,
            "pending": {"nuevos": [p.name for p in new], "cambiados": [p.name for _, p in changed],
                        "eliminados": [d.original_name for d in gone]}}


# ── Frontend ──────────────────────────────────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

if os.path.exists(FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="assets")

    @app.get("/")
    async def serve_landing():
        return FileResponse(os.path.join(FRONTEND_DIR, "landing.html"))

    @app.get("/login")
    async def serve_login():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/dashboard")
    async def serve_dashboard():
        return FileResponse(os.path.join(FRONTEND_DIR, "dashboard.html"))


# ── Estado de todos los servicios ─────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    pg, emb, vision = await asyncio.gather(
        run_in_threadpool(vector_store.status), run_in_threadpool(embedding_service.status), run_in_threadpool(vision_service.status))
    ok = pg.get("online") and pg.get("pgvector_available") and emb.get("online")
    return {
        "status": "healthy" if ok else "degraded", "service": APP_NAME, "version": "2.0.0",
        "embeddings": emb, "vector_db": {"engine": "PostgreSQL + pgvector", **pg}, "vision": vision,
        "media": media_service.status(), "knowledge_base": kb_service.kb_info(), "max_pdf_pages": MAX_PDF_PAGES,
    }
