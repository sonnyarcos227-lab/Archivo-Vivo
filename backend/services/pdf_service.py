"""
pdf_service.py – PDFs: extracción de texto (con OCR), descripción de imágenes
                  (qwen2.5vl), chunking y embeddings con EmbeddingGemma 2
                  (llama.cpp) guardados en PostgreSQL + pgvector.
"""

import os
import re
import json
import uuid
from typing import List, Tuple

import numpy as np
import pymupdf
from dotenv import load_dotenv

try:
    from services import vector_store, vision_service, embedding_service
except ImportError:
    import vector_store, vision_service, embedding_service

load_dotenv()

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(_BACKEND_DIR, "uploads")   # archivos antiguos (antes de base_conocimiento)
# Copia portable de los vectores (json + npy) para poder llevar el proyecto a otro PC
# y reconstruir PostgreSQL con migrar_a_pgvector.py. La búsqueda NO usa estos archivos.
VECTOR_BACKUP_DIR = os.path.join(_BACKEND_DIR, "chroma_data")
os.makedirs(VECTOR_BACKUP_DIR, exist_ok=True)

os.makedirs(UPLOAD_DIR, exist_ok=True)

_ocr_engine = None


def get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
            print("[OK] Motor RapidOCR inicializado para PDFs escaneados")
        except Exception as e:
            print(f"[WARN] No se pudo inicializar RapidOCR: {e}")
            _ocr_engine = None
    return _ocr_engine


def ocr_page_image(page) -> str:
    """Render a PDF page to image and perform OCR using RapidOCR."""
    engine = get_ocr_engine()
    if not engine:
        return ""
    try:
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        result, _ = engine(img_bytes)
        if result:
            lines = [line[1] for line in result if line and len(line) > 1]
            return "\n".join(lines).strip()
    except Exception as e:
        print(f"[WARN] Error en OCR de página: {e}")
    return ""


# ── Text Extraction (Digital + Automatic OCR for Scans) ───────────────────────
def extract_text_from_pdf(file_path: str) -> Tuple[List[str], int]:
    """
    Extract text from each page of a PDF.
    Automatically applies OCR when a page is scanned or contains no digital text.
    Returns: (list of page texts, total page count)
    """
    doc = pymupdf.open(file_path)
    total_pages = len(doc)
    pages = []

    for page_idx in range(total_pages):
        page = doc[page_idx]
        text = page.get_text() or ""
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text).strip()

        # Si la página tiene menos de 25 caracteres (escaneo o imagen pura), aplicar OCR
        if len(text) < 25:
            ocr_text = ocr_page_image(page)
            if ocr_text:
                text = ocr_text

        pages.append(text)

    doc.close()
    return pages, total_pages


# ── Imágenes insertadas → descripciones con el modelo de visión ──────────────
VISION_MAX_IMAGES = int(os.getenv("VISION_MAX_IMAGES", "6"))
VISION_MIN_SIDE = int(os.getenv("VISION_MIN_SIDE", "120"))   # ignora íconos y logos pequeños


def extract_pdf_images(file_path: str, max_images: int = VISION_MAX_IMAGES) -> List[Tuple[int, bytes]]:
    """
    Saca las imágenes insertadas en el PDF como PNG (máx. 1024 px de lado).
    Ignora íconos pequeños, imágenes repetidas (logos en cada página) y las
    páginas escaneadas completas (esas ya se leen con OCR).
    Devuelve [(número_de_página, bytes_png), ...].
    """
    doc = pymupdf.open(file_path)
    found: List[Tuple[int, bytes]] = []
    seen = set()
    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_area = abs(page.rect) or 1
            scanned = len((page.get_text() or "").strip()) < 25
            for img in page.get_images(full=True):
                xref = img[0]
                if xref in seen:
                    continue
                try:
                    rects = page.get_image_rects(xref)
                    if not rects:
                        continue  # declarada en la página pero no dibujada en ella
                    seen.add(xref)
                    cover = max(abs(r) / page_area for r in rects)
                    if cover > 0.85 or (scanned and cover > 0.4):
                        continue  # escaneo de la página: su texto ya lo cubre el OCR
                    pix = pymupdf.Pixmap(doc, xref)
                    if pix.n - pix.alpha >= 4:          # CMYK → RGB
                        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                    if pix.alpha:
                        pix = pymupdf.Pixmap(pix, 0)    # sin canal alfa
                    if min(pix.width, pix.height) < VISION_MIN_SIDE:
                        continue
                    while max(pix.width, pix.height) > 1024:
                        pix.shrink(1)                   # reduce a la mitad
                    found.append((page_idx + 1, pix.tobytes("png")))
                except Exception as e:
                    print(f"[WARN] No se pudo extraer una imagen de la página {page_idx + 1}: {e}")
                if len(found) >= max_images:
                    return found
    finally:
        doc.close()
    return found


def describe_pdf_images(file_path: str, start_index: int = 0) -> List[dict]:
    """
    Describe cada imagen con el modelo de visión (Ollama qwen2.5vl) y devuelve
    fragmentos listos para guardar junto al texto del PDF.
    """
    if not vision_service.is_available():
        print(f"[INFO] Modelo de visión '{vision_service.VISION_MODEL}' no disponible: se omiten las imágenes.")
        return []
    images = extract_pdf_images(file_path)
    chunks = []
    for n, (page_num, png) in enumerate(images, 1):
        print(f"[INFO] Describiendo imagen {n}/{len(images)} (página {page_num})...")
        last = n == len(images)
        desc = vision_service.describe_image(png, keep_alive="0s" if last else "60s")
        if desc:
            chunks.append({
                "text": f"[Imagen en la página {page_num}] {desc}",
                "page": page_num,
                "chunk_index": start_index + len(chunks),
                "kind": "image",
            })
    print(f"[OK] {len(chunks)} imagen(es) descritas con {vision_service.VISION_MODEL}")
    return chunks


# ── Intelligent Chunking with Overlap ─────────────────────────────────────────
def chunk_text_with_overlap(
    pages: List[str],
    chunk_size: int = 800,
    overlap: int = 150,
) -> List[dict]:
    """
    Split page texts into overlapping chunks for better retrieval.
    Each chunk carries its source page number(s).
    """
    chunks = []
    for page_idx, page_text in enumerate(pages):
        if not page_text.strip():
            continue

        page_num = page_idx + 1
        words = page_text.split()

        if len(words) <= chunk_size:
            chunks.append({
                "text": page_text,
                "page": page_num,
                "chunk_index": len(chunks),
            })
        else:
            start = 0
            while start < len(words):
                end = start + chunk_size
                chunk_words = words[start:end]
                chunk_text = " ".join(chunk_words)
                chunks.append({
                    "text": chunk_text,
                    "page": page_num,
                    "chunk_index": len(chunks),
                })
                if end >= len(words):
                    break  # evita un último chunk que ya estaba contenido en el anterior
                start += chunk_size - overlap

    return chunks


# ── Vectorización y almacenamiento en PostgreSQL + pgvector ──────────────────
def vectorize_and_store(chunks: List[dict], collection_name: str, title: str = None) -> int:
    """
    Genera el embedding de cada fragmento con EmbeddingGemma 2 (llama.cpp) y lo
    guarda en PostgreSQL + pgvector. Sirve para PDF, audio y video.
    """
    if not chunks:
        return 0
    embeddings = embedding_service.embed_documents([c["text"] for c in chunks], title=title)
    stored = vector_store.insert_chunks(collection_name, chunks, embeddings)
    save_vector_backup(collection_name, chunks, embeddings)
    return stored


def save_vector_backup(collection_name: str, chunks: List[dict], embeddings) -> None:
    """Copia portable (json + npy) para llevar la base a otro PC. La búsqueda no la usa."""
    try:
        with open(os.path.join(VECTOR_BACKUP_DIR, f"{collection_name}.json"), "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False)
        np.save(os.path.join(VECTOR_BACKUP_DIR, f"{collection_name}.npy"), np.asarray(embeddings, dtype=np.float32))
    except Exception as e:
        print(f"[WARN] No se pudo guardar el respaldo portable de vectores: {e}")


# ── Búsqueda semántica ────────────────────────────────────────────────────────
def query_collection(collection_name, query_text: str, top_k: int = 5) -> List[dict]:
    """
    Fragmentos más parecidos a la pregunta, dentro de un archivo o en toda la
    base (collection_name=None). La similitud coseno la calcula pgvector (<=>).
    """
    query_vec = embedding_service.embed_query(query_text)
    return vector_store.search(collection_name, query_vec, top_k=top_k)


# ── Full Ingestion Pipeline ───────────────────────────────────────────────────
def save_uploaded_file(file_bytes: bytes, original_filename: str, user_id: str) -> Tuple[str, str]:
    """Save the uploaded PDF to disk and return the file path and unique filename."""
    safe_name = re.sub(r'[^\w\-.]', '_', original_filename)
    unique_name = f"{user_id}_{uuid.uuid4().hex[:8]}_{safe_name}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)
    with open(file_path, "wb") as f:
        f.write(file_bytes)
    return file_path, unique_name


def delete_document_data(collection_name: str, filename: str = None, file_path: str = None):
    """Elimina los vectores del archivo en PostgreSQL, su copia portable y el archivo."""
    vector_store.delete_collection(collection_name)
    for ext in (".json", ".npy"):
        backup = os.path.join(VECTOR_BACKUP_DIR, f"{collection_name}{ext}")
        if os.path.exists(backup):
            os.remove(backup)
    path = file_path or (os.path.join(UPLOAD_DIR, filename) if filename else None)
    if path and os.path.isfile(path):
        os.remove(path)
