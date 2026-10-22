"""
exportar_vectores.py – Copia de PostgreSQL + pgvector a backend/chroma_data/
(json + npy) los fragmentos y vectores de cada archivo de la base.

Sirve para llevar el proyecto a otro PC (por ejemplo, por GitHub): allá run.bat
ejecuta migrar_a_pgvector.py y vuelve a cargar todo sin re-procesar los
PDFs, audios y videos.

Uso (desde la carpeta backend, con Docker Desktop abierto):
    py exportar_vectores.py
"""
import os
import sys
import json
import sqlite3
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from services import vector_store  # noqa: E402

CHROMA_DIR = BACKEND_DIR / "chroma_data"
DB_PATH = BACKEND_DIR / "chatpdf_expert.db"


def main():
    CHROMA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        docs = conn.execute("SELECT original_name, collection_name FROM documents").fetchall()
    if not docs:
        print("No hay archivos registrados.")
        return
    exportados = 0
    for name, collection in docs:
        rows = vector_store.get_chunks(collection, with_vectors=True)
        if not rows:
            print(f"!  {name}: no tiene fragmentos en PostgreSQL")
            continue
        chunks = [{"text": r["text"], "page": r["page"], "chunk_index": r["chunk_index"], "kind": r["kind"],
                   "start_sec": r["start_sec"], "end_sec": r["end_sec"]} for r in rows]
        vectors = np.array([json.loads(r["embedding"]) for r in rows], dtype=np.float32)
        with open(CHROMA_DIR / f"{collection}.json", "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False)
        np.save(CHROMA_DIR / f"{collection}.npy", vectors)
        print(f"OK {name}: {len(chunks)} fragmentos exportados ({vectors.shape[1]} dimensiones)")
        exportados += 1
    print(f"\nListo: {exportados} archivo(s) con su copia en chroma_data. Ya puedes subir el proyecto.")


if __name__ == "__main__":
    main()
