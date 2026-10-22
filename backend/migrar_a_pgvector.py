"""
migrar_a_pgvector.py – Deja la base de conocimiento lista en PostgreSQL + pgvector.

Lo ejecuta run.bat antes de abrir la app. Para cada archivo registrado:
  1. Si es un PDF del ChatPDF anterior (estaba en backend/uploads/), lo mueve a
     base_conocimiento/pdf/ para que forme parte de la carpeta de la base.
  2. Si sus fragmentos no están en la tabla kb_fragmentos, los carga desde la
     copia portable (backend/chroma_data/*.json + *.npy). Si esos vectores son
     de otro modelo (p. ej. 384 dimensiones del modelo viejo), los vuelve a
     calcular con EmbeddingGemma 2 (llama.cpp debe estar encendido).

Uso (desde la carpeta backend):
    py migrar_a_pgvector.py
"""
import os
import sys
import json
import shutil
import sqlite3
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from services import vector_store, embedding_service, kb_service  # noqa: E402
from services.pdf_service import save_vector_backup, UPLOAD_DIR  # noqa: E402

CHROMA_DIR = BACKEND_DIR / "chroma_data"
DB_PATH = BACKEND_DIR / "chatpdf_expert.db"
NEW_COLS = [("media_type", "VARCHAR(10) DEFAULT 'pdf'"), ("source_path", "VARCHAR(1000)"),
            ("duration_sec", "FLOAT"), ("file_sig", "VARCHAR(100)")]


def asegurar_columnas(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    for col, ddl in NEW_COLS:
        if col not in cols:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {ddl}")
    conn.commit()


def mover_pdfs_antiguos(conn):
    rows = conn.execute("SELECT id, filename, original_name FROM documents "
                        "WHERE source_path IS NULL OR source_path = ''").fetchall()
    for doc_id, filename, name in rows:
        old = Path(UPLOAD_DIR) / filename
        if not old.exists():
            print(f"!  {name}: el PDF original no está en backend/uploads (queda solo con sus fragmentos)")
            continue
        dest = kb_service.KB_DIR / "pdf" / name
        n = 2
        while dest.exists():
            dest = kb_service.KB_DIR / "pdf" / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        shutil.move(str(old), dest)
        rel = kb_service.rel_path(dest)
        conn.execute("UPDATE documents SET source_path=?, filename=?, media_type=COALESCE(media_type,'pdf'), file_sig=? WHERE id=?",
                     (rel, rel, kb_service.file_sig(dest), doc_id))
        print(f"OK {name}: movido a base_conocimiento/{rel}")
    conn.commit()


def cargar_vectores(conn) -> int:
    docs = conn.execute("SELECT original_name, collection_name FROM documents").fetchall()
    migrados = 0
    for name, collection in docs:
        ya = vector_store.count_chunks(collection)
        if ya:
            print(f"-  {name}: ya está en pgvector ({ya} fragmentos)")
            continue
        meta, vecs = CHROMA_DIR / f"{collection}.json", CHROMA_DIR / f"{collection}.npy"
        if not meta.exists():
            print(f"!  {name}: sin copia en chroma_data; usa 'Indexar carpeta' o vuelve a subirlo")
            continue
        chunks = json.loads(meta.read_text(encoding="utf-8"))
        for i, c in enumerate(chunks):
            c.setdefault("chunk_index", i)
            c.setdefault("kind", "image" if c.get("text", "").startswith("[Imagen en la página") else "text")
        emb = np.load(vecs) if vecs.exists() else None
        if emb is None or emb.ndim != 2 or emb.shape[1] != embedding_service.EMBED_DIM or len(emb) != len(chunks):
            print(f"~  {name}: recalculando {len(chunks)} vectores con EmbeddingGemma 2…")
            try:
                emb = np.asarray(embedding_service.embed_documents([c["text"] for c in chunks], title=name), dtype=np.float32)
            except embedding_service.EmbeddingUnavailable as e:
                print(f"!  {name}: {e}")
                continue
            save_vector_backup(collection, chunks, emb)
        n = vector_store.insert_chunks(collection, chunks, [e.tolist() for e in emb])
        print(f"OK {name}: {n} fragmentos en pgvector ({emb.shape[1]} dimensiones)")
        migrados += 1
    return migrados


def main():
    if not DB_PATH.exists():
        print("Base de datos nueva: nada que migrar.")
        return
    with sqlite3.connect(DB_PATH) as conn:
        if not conn.execute("SELECT name FROM sqlite_master WHERE name='documents'").fetchone():
            print("Base de datos nueva: nada que migrar.")
            return
        asegurar_columnas(conn)
        mover_pdfs_antiguos(conn)
        try:
            n = cargar_vectores(conn)
        except vector_store.VectorDBUnavailable as e:
            print(f"[ERROR] {e}")
            return
    print(f"\nListo: {n} archivo(s) cargados en pgvector.")


if __name__ == "__main__":
    main()
