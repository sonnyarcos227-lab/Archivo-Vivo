"""
vector_store.py – Base de datos vectorial: PostgreSQL + pgvector.

Tabla (se crea sola):
    kb_fragmentos(
        id, collection_name, chunk_index,
        page        -- página (PDF)
        start_sec   -- segundo de inicio (audio / video)
        end_sec     -- segundo de fin    (audio / video)
        kind        -- 'text' | 'image' | 'transcript' | 'frame'
        content     -- el texto del fragmento
        embedding   -- vector(768) de EmbeddingGemma 2
    )
La búsqueda usa distancia coseno (<=>) con índice HNSW, ya sea dentro de un
archivo (collection_name) o en toda la base de conocimiento.
"""

import os
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from dotenv import load_dotenv

load_dotenv()
_backend_env = Path(__file__).resolve().parent.parent / ".env"
if _backend_env.exists():
    load_dotenv(dotenv_path=_backend_env)

PGVECTOR_URL = os.getenv("PGVECTOR_URL", "postgresql://postgres:postgres@localhost:5432/chatpdf")
TABLE = os.getenv("PGVECTOR_TABLE", "kb_fragmentos")

_schema_ready_dim: Optional[int] = None


def _to_pgvector(vec: List[float]) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


class VectorDBUnavailable(RuntimeError):
    pass


def _friendly(e: Exception) -> VectorDBUnavailable:
    msg = str(e)
    if "refused" in msg or "rechaz" in msg or "timeout" in msg.lower():
        return VectorDBUnavailable(
            "No se pudo conectar a PostgreSQL (localhost:5432). "
            "¿Está abierto Docker Desktop y corriendo el contenedor 'pgvector'? Prueba: docker start pgvector"
        )
    if "password" in msg or "contraseña" in msg:
        return VectorDBUnavailable("PostgreSQL rechazó el usuario/contraseña. Revisa PGVECTOR_URL en backend/.env")
    return VectorDBUnavailable(f"Error de PostgreSQL: {msg}")


def _ensure_database_exists():
    parsed = urlparse(PGVECTOR_URL)
    db_name = (parsed.path or "/").lstrip("/") or "postgres"
    try:
        psycopg.connect(PGVECTOR_URL, connect_timeout=5).close()
        return
    except psycopg.OperationalError as e:
        if "does not exist" not in str(e) and "no existe" not in str(e):
            raise _friendly(e) from e
    admin_url = parsed._replace(path="/postgres").geturl()
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    print(f"[OK] Base de datos '{db_name}' creada en PostgreSQL")


def get_connection() -> psycopg.Connection:
    try:
        return psycopg.connect(PGVECTOR_URL, connect_timeout=5)
    except psycopg.OperationalError as e:
        if "does not exist" in str(e) or "no existe" in str(e):
            _ensure_database_exists()          # PC nuevo: se crea la base y se reintenta
            return psycopg.connect(PGVECTOR_URL, connect_timeout=5)
        raise _friendly(e) from e


def ensure_schema(dim: int):
    """Crea la extensión, la tabla y los índices (una vez por proceso)."""
    global _schema_ready_dim
    if _schema_ready_dim == dim:
        return
    _ensure_database_exists()
    t = sql.Identifier(TABLE)
    with get_connection() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS {t} (
                id              BIGSERIAL PRIMARY KEY,
                collection_name TEXT NOT NULL,
                chunk_index     INTEGER NOT NULL,
                page            INTEGER,
                start_sec       REAL,
                end_sec         REAL,
                kind            TEXT NOT NULL DEFAULT 'text',
                content         TEXT NOT NULL,
                embedding       vector({dim}) NOT NULL,
                created_at      TIMESTAMPTZ DEFAULT now()
            )
        """).format(t=t, dim=sql.Literal(dim)))
        existing = conn.execute(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = %s::regclass AND a.attname = 'embedding'", (TABLE,)
        ).fetchone()[0]
        if existing != f"vector({dim})":
            raise RuntimeError(
                f"La tabla {TABLE} tiene columna {existing} pero el modelo de embeddings produce "
                f"vector({dim}). Borra la tabla (DROP TABLE {TABLE};) y vuelve a indexar."
            )
        conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {i} ON {t} (collection_name)").format(
            i=sql.Identifier(f"idx_{TABLE}_collection"), t=t))
        conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {i} ON {t} USING hnsw (embedding vector_cosine_ops)").format(
            i=sql.Identifier(f"idx_{TABLE}_hnsw"), t=t))
        conn.commit()
    _schema_ready_dim = dim


def insert_chunks(collection_name: str, chunks: List[dict], embeddings: List[List[float]]) -> int:
    if not chunks:
        return 0
    ensure_schema(len(embeddings[0]))
    rows = [
        (collection_name, c.get("chunk_index", i), c.get("page"), c.get("start_sec"), c.get("end_sec"),
         c.get("kind", "text"), c["text"], _to_pgvector(e))
        for i, (c, e) in enumerate(zip(chunks, embeddings))
    ]
    q_del = sql.SQL("DELETE FROM {t} WHERE collection_name = %s").format(t=sql.Identifier(TABLE))
    q_ins = sql.SQL(
        "INSERT INTO {t} (collection_name, chunk_index, page, start_sec, end_sec, kind, content, embedding) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)"
    ).format(t=sql.Identifier(TABLE))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(q_del, (collection_name,))
            cur.executemany(q_ins, rows)
        conn.commit()
    return len(rows)


def _row_to_dict(r) -> dict:
    return {
        "collection_name": r[0], "text": r[1], "page": r[2] or 0,
        "start_sec": r[3], "end_sec": r[4], "kind": r[5], "score": round(float(r[6]), 4),
        "chunk_index": r[7],
    }


def search(collection_name: Optional[str], query_embedding: List[float], top_k: int = 5) -> List[dict]:
    """Busca en un archivo (collection_name) o en toda la base (collection_name=None)."""
    ensure_schema(len(query_embedding))
    qv = _to_pgvector(query_embedding)
    t = sql.Identifier(TABLE)
    cols = sql.SQL("collection_name, content, page, start_sec, end_sec, kind, 1 - (embedding <=> %s::vector) AS score, chunk_index")
    if collection_name:
        q = sql.SQL("SELECT {c} FROM {t} WHERE collection_name = %s ORDER BY embedding <=> %s::vector LIMIT %s").format(c=cols, t=t)
        params = (qv, collection_name, qv, top_k)
    else:
        q = sql.SQL("SELECT {c} FROM {t} ORDER BY embedding <=> %s::vector LIMIT %s").format(c=cols, t=t)
        params = (qv, qv, top_k)
    with get_connection() as conn:
        rows = conn.execute(q, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_collection(collection_name: str):
    try:
        with get_connection() as conn:
            conn.execute(sql.SQL("DELETE FROM {t} WHERE collection_name = %s").format(t=sql.Identifier(TABLE)), (collection_name,))
            conn.commit()
    except psycopg.errors.UndefinedTable:
        pass


def get_chunks(collection_name: str, with_vectors: bool = False) -> List[dict]:
    """Todos los fragmentos de un archivo, en orden."""
    extra = sql.SQL(", embedding::text") if with_vectors else sql.SQL("")
    q = sql.SQL(
        "SELECT chunk_index, page, start_sec, end_sec, kind, content, vector_dims(embedding){x} "
        "FROM {t} WHERE collection_name = %s ORDER BY chunk_index"
    ).format(x=extra, t=sql.Identifier(TABLE))
    try:
        with get_connection() as conn:
            rows = conn.execute(q, (collection_name,)).fetchall()
    except psycopg.errors.UndefinedTable:
        return []
    out = []
    for r in rows:
        d = {"chunk_index": r[0], "page": r[1] or 0, "start_sec": r[2], "end_sec": r[3],
             "kind": r[4], "text": r[5], "dims": r[6]}
        if with_vectors:
            d["embedding"] = r[7]
        out.append(d)
    return out


def count_chunks(collection_name: str) -> int:
    try:
        with get_connection() as conn:
            return conn.execute(sql.SQL("SELECT count(*) FROM {t} WHERE collection_name = %s").format(
                t=sql.Identifier(TABLE)), (collection_name,)).fetchone()[0]
    except psycopg.errors.UndefinedTable:
        return 0


def status() -> dict:
    try:
        _ensure_database_exists()
        with get_connection() as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            ext = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
            available = conn.execute("SELECT default_version FROM pg_available_extensions WHERE name = 'vector'").fetchone()
            total, by_kind = None, {}
            if ext and conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0]:
                t = sql.Identifier(TABLE)
                total = conn.execute(sql.SQL("SELECT count(*) FROM {t}").format(t=t)).fetchone()[0]
                by_kind = dict(conn.execute(sql.SQL("SELECT kind, count(*) FROM {t} GROUP BY kind").format(t=t)).fetchall())
        return {
            "online": True, "postgres_version": version, "table": TABLE,
            "pgvector_installed": bool(ext), "pgvector_version": ext[0] if ext else None,
            "pgvector_available": bool(available), "total_chunks": total, "chunks_by_kind": by_kind,
        }
    except Exception as e:
        return {"online": False, "error": str(e)}
