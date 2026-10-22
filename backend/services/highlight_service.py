"""
highlight_service.py – "Chunks de marcado": ubica en el archivo original el
fragmento que usó la respuesta, para subrayarlo en el visor.

  • PDF digital:   palabras de la página con sus coordenadas (PyMuPDF) alineadas
                   con el texto del fragmento (difflib) → recuadros por renglón.
  • PDF escaneado: lo mismo, pero las palabras salen del OCR (RapidOCR).
  • Imagen PDF:    el recuadro de la imagen dentro de la página.
  • Imagen suelta: renglones del OCR que contienen las palabras clave.
  • Audio / video: tramos de tiempo + palabras clave para la transcripción.

Las coordenadas se devuelven normalizadas (0 a 1) respecto a la página o imagen,
así el navegador las dibuja encima sin importar el zoom.
"""

import io
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import List, Optional, Tuple

import pymupdf

try:
    from services import pdf_service, vector_store
except ImportError:
    import pdf_service, vector_store

STOP = set("""
a al algo algun alguna algunas alguno algunos ante antes aqui asi aun cada como con contra cual cuales cuando
de del desde donde dos el ella ellas ello ellos en entre era eran es esa esas ese eso esos esta estaba estado
estan estar este esto estos fue fueron ha habia han hasta hay la las le les lo los mas me mi mientras muy nada
ni no nos o otra otras otro otros para pero poco por porque que quien se segun ser si sido sin sobre solo son
su sus tal tambien tanto te tiene tienen todo todos tu un una uno unos usted y ya yo cual cuanto cuanta cuantos
dice dicen archivo documento pagina minuto imagen texto base segun menciona fragmento contexto respuesta pregunta
""".split())


# ── Utilidades de texto ───────────────────────────────────────────────────────
def norm(word: str) -> str:
    w = unicodedata.normalize("NFD", word.lower())
    w = "".join(c for c in w if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ]", "", w)


def tokens_of(text: str) -> List[str]:
    text = re.sub(r"^\[[^\]]*\]\s*", "", text or "")          # quita "[Imagen en la página 2]" etc.
    return [t for t in (norm(w) for w in re.split(r"[\s/|·,;:()\[\]]+", text)) if t]


def key_terms(text: str) -> List[str]:
    """Palabras clave de la pregunta + respuesta (sin palabras vacías)."""
    out, seen = [], set()
    text = re.sub(r"\d{1,2}:\d{2}(?::\d{2})?", " ", text or "")       # marcas de tiempo, no son palabras clave
    for t in tokens_of(text):
        is_num = bool(re.fullmatch(r"\d{3,}", t))
        if (len(t) >= 4 and t not in STOP) or is_num:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out[:40]


def _term_rank(tok: str, terms: List[str]) -> int:
    """Posición de la palabra clave que coincide (las de la pregunta van primero); -1 si ninguna."""
    if not tok or len(tok) < 3:
        return -1
    for i, t in enumerate(terms):
        if tok == t or (len(t) >= 5 and len(tok) >= 5 and (tok.startswith(t[:5]) or t.startswith(tok[:5]))):
            return i
    return -1


def _is_key(tok: str, terms: List[str]) -> bool:
    return _term_rank(tok, terms) >= 0


# ── Palabras con coordenadas ─────────────────────────────────────────────────
Word = Tuple[float, float, float, float, str, Tuple[int, int]]   # x0, y0, x1, y1, texto, (bloque, renglón)


def _ocr_lines(png: bytes) -> List[Tuple[Tuple[float, float, float, float], str]]:
    engine = pdf_service.get_ocr_engine()
    if not engine:
        return []
    result, _ = engine(png)
    lines = []
    for item in result or []:
        try:
            box, text = item[0], str(item[1])
        except Exception:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        lines.append(((min(xs), min(ys), max(xs), max(ys)), text))
    return lines


def _split_line(bbox, text: str, line_id) -> List[Word]:
    """Reparte el ancho de un renglón de OCR entre sus palabras (proporcional a las letras)."""
    x0, y0, x1, y1 = bbox
    words = text.split()
    total = sum(len(w) for w in words) + max(len(words) - 1, 0)
    out, cur = [], x0
    for w in words:
        width = (x1 - x0) * len(w) / max(total, 1)
        out.append((cur, y0, cur + width, y1, w, line_id))
        cur += width + (x1 - x0) / max(total, 1)
    return out


def _page_words(page) -> Tuple[List[Word], float, float, bool]:
    """Palabras de la página en coordenadas PDF. Si la página es un escaneo, usa OCR."""
    W, H = page.rect.width, page.rect.height
    raw = page.get_text("words")
    if len(" ".join(w[4] for w in raw)) >= 25:
        return [(w[0], w[1], w[2], w[3], w[4], (w[5], w[6])) for w in raw], W, H, False
    zoom = 150 / 72
    pix = page.get_pixmap(dpi=150)
    words: List[Word] = []
    for i, (bbox, text) in enumerate(_ocr_lines(pix.tobytes("png"))):
        x0, y0, x1, y1 = (v / zoom for v in bbox)
        words.extend(_split_line((x0, y0, x1, y1), text, (0, i)))
    return words, W, H, True


@lru_cache(maxsize=64)
def _page_words_cached(path: str, mtime: float, page_index: int):
    """Las palabras de cada página se calculan una vez (el OCR de un escaneo tarda varios segundos)."""
    doc = pymupdf.open(path)
    try:
        return _page_words(doc[page_index])
    finally:
        doc.close()


def _boxes(words: List[Word], idxs: List[int], W: float, H: float, key: bool = False) -> List[dict]:
    """Une palabras consecutivas del mismo renglón en un solo recuadro."""
    boxes, cur, last_line = [], None, None
    for i in sorted(idxs):
        x0, y0, x1, y1, _, line = words[i]
        if cur and line == last_line and x0 - cur[2] < (y1 - y0) * 1.2:
            cur = [cur[0], min(cur[1], y0), x1, max(cur[3], y1)]
        else:
            if cur:
                boxes.append(cur)
            cur = [x0, y0, x1, y1]
        last_line = line
    if cur:
        boxes.append(cur)
    return [{"x": b[0] / W, "y": b[1] / H, "w": (b[2] - b[0]) / W, "h": (b[3] - b[1]) / H, "key": key}
            for b in boxes]


def _align(words: List[Word], chunk_text: str) -> List[int]:
    """Índices de las palabras de la página que forman el fragmento."""
    page_norm = [norm(w[4]) for w in words]
    chunk_norm = tokens_of(chunk_text)
    if not page_norm or not chunk_norm:
        return []
    sm = SequenceMatcher(None, page_norm, chunk_norm, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    min_size = 2 if len(chunk_norm) > 12 else 1
    idxs = []
    for b in blocks:
        if b.size >= min_size:
            idxs.extend(range(b.a, b.a + b.size))
    if not idxs:
        return []
    # descarta coincidencias sueltas muy lejos del bloque principal
    lo, hi = min(idxs), max(idxs)
    span = len(chunk_norm) * 1.6
    if hi - lo > span:
        center = sorted(idxs)[len(idxs) // 2]
        idxs = [i for i in idxs if abs(i - center) <= span / 2]
    lo, hi = min(idxs), max(idxs)
    # rellena huecos pequeños (palabras partidas, guiones, OCR imperfecto)
    return [i for i in range(lo, hi + 1) if page_norm[i]]


def _image_boxes(page) -> List[dict]:
    W, H = page.rect.width, page.rect.height
    area = abs(page.rect) or 1
    out = []
    for img in page.get_images(full=True):
        try:
            for r in page.get_image_rects(img[0]):
                cover = abs(r) / area
                if 0.01 < cover < 0.85:
                    out.append({"x": r.x0 / W, "y": r.y0 / H, "w": r.width / W, "h": r.height / H, "key": False})
        except Exception:
            pass
    return out


# ── Pasajes: párrafos de la página puntuados contra la pregunta ──────────────
def question_phrases(question: str) -> List[Tuple[str, str]]:
    """Frases tipo "observación 3", "artículo 12", "capítulo dos": palabra + número/ordinal."""
    toks = tokens_of(question)
    out = []
    for a, b in zip(toks, toks[1:]):
        if len(a) >= 4 and a not in STOP and (b.isdigit() or b in ORDINALS):
            out.append((a, ORDINALS.get(b, b)))
        elif a in ORDINALS and len(b) >= 4 and b not in STOP and not b.isdigit():   # "la tercera observación"
            out.append((b, ORDINALS[a]))
    return out


ORDINALS = {"uno": "1", "primera": "1", "primero": "1", "dos": "2", "segunda": "2", "segundo": "2",
            "tres": "3", "tercera": "3", "tercero": "3", "cuatro": "4", "cuarta": "4", "cuarto": "4",
            "cinco": "5", "quinta": "5", "quinto": "5", "seis": "6", "sexta": "6", "siete": "7", "ocho": "8",
            "nueve": "9", "diez": "10"}


def _same_word(a: str, b: str) -> bool:
    return a == b or (len(a) >= 5 and len(b) >= 5 and a[:6] == b[:6])


def _paragraphs(words: List[Word]) -> List[List[int]]:
    """Agrupa las palabras en renglones y los renglones en párrafos según el espacio vertical entre ellos."""
    lines: dict = {}
    for i, w in enumerate(words):
        lines.setdefault(w[5], []).append(i)
    ordered = sorted(lines.values(), key=lambda idxs: (min(words[i][1] for i in idxs), min(words[i][0] for i in idxs)))
    paras, cur, prev = [], [], None
    for idxs in ordered:
        idxs = sorted(idxs, key=lambda i: words[i][0])
        y0 = min(words[i][1] for i in idxs)
        y1 = max(words[i][3] for i in idxs)
        h = max(y1 - y0, 1)
        if prev and (y0 - prev[1] > max(5.0, 0.6 * h) or y0 < prev[0] - h):   # hueco o salto de columna
            paras.append(cur)
            cur = []
        cur.extend(idxs)
        prev = (y0, y1)
    if cur:
        paras.append(cur)
    return paras


def _phrase_at(toks: List[str], phrase: Tuple[str, str], limit: Optional[int] = None) -> int:
    """Posición donde aparece la frase (palabra seguida del número) o -1."""
    a, b = phrase
    rng = range(len(toks) - 1) if limit is None else range(min(limit, len(toks) - 1))
    for k in rng:
        if _same_word(toks[k], a) and toks[k + 1] == b:
            return k
    return -1


def _starts_other_section(toks: List[str], phrase: Tuple[str, str]) -> bool:
    """El párrafo arranca otra sección del mismo tipo (p. ej. "Observación 4" cuando se pidió la 3)."""
    a, b = phrase
    for k in range(min(3, len(toks) - 1)):
        if _same_word(toks[k], a) and toks[k + 1].isdigit() and toks[k + 1] != b:
            return True
    return False


def _score_paragraph(toks: List[str], q_terms, a_terms, phrases) -> Tuple[float, bool]:
    q_hit = {t for t in q_terms if any(_same_word(x, t) for x in toks)}
    a_hit = {t for t in a_terms if any(_same_word(x, t) for x in toks)}
    score = 3 * len(q_hit) + min(len(a_hit), 12)
    heading = False
    for ph in phrases:
        pos = _phrase_at(toks, ph)
        if pos >= 0:
            score += 14
            if pos <= 3:
                score += 6
                heading = True
    return score, heading


def _page_is_digital(path: str, page_index: int) -> bool:
    doc = pymupdf.open(path)
    try:
        return len((doc[page_index].get_text() or "").strip()) >= 25
    finally:
        doc.close()


def best_passage(path: str, pages: List[int], cited_page: int, question: str, answer: str):
    """Busca en las páginas candidatas el pasaje que mejor responde a la pregunta."""
    import os
    mtime = os.path.getmtime(path)
    q_terms = key_terms(question)
    a_terms = [t for t in key_terms(answer) if t not in q_terms]
    phrases = question_phrases(question)
    best = None
    for pn in pages:
        words, W, H, ocr = _page_words_cached(path, mtime, pn - 1)
        paras = _paragraphs(words)
        ptoks = [[norm(words[i][4]) for i in para] for para in paras]
        for k, toks in enumerate(ptoks):
            sc, heading = _score_paragraph(toks, q_terms, a_terms, phrases)
            sc += 2 if pn == cited_page else 0
            if sc > 0 and (best is None or sc > best["score"]):
                best = {"score": sc, "page": pn, "k": k, "heading": heading, "words": words, "W": W, "H": H,
                        "ocr": ocr, "paras": paras, "ptoks": ptoks}
    if not best:
        return None
    paras, ptoks, k = best["paras"], best["ptoks"], best["k"]
    chosen = [k]
    if best["heading"] and phrases:
        # sección completa: desde "Observación 3" hasta la siguiente "Observación N" (o el fin de la página)
        ph = next(p for p in phrases if _phrase_at(ptoks[k], p, 4) >= 0)
        for j in range(k + 1, min(len(paras), k + 8)):
            if _starts_other_section(ptoks[j], ph):
                break
            chosen.append(j)
    else:
        # párrafos vecinos que también hablan de lo mismo
        def sc(j):
            return _score_paragraph(ptoks[j], q_terms, a_terms, phrases)[0]
        base = sc(k)
        for j in (k - 1, k + 1):
            if 0 <= j < len(paras) and sc(j) >= max(4, 0.6 * base):
                chosen.append(j)
    idxs = sorted(i for j in chosen for i in paras[j])
    words = best["words"]
    keys = [i for i in idxs if _is_key(norm(words[i][4]), q_terms)]
    for ph in phrases:                                   # subraya también el número de la frase ("3")
        for j in chosen:
            pos = _phrase_at(ptoks[j], ph)
            if pos >= 0:
                keys += [paras[j][pos], paras[j][pos + 1]]
    if not keys:
        keys = [i for i in idxs if _is_key(norm(words[i][4]), a_terms)][:14]
    return {"page": best["page"], "ocr": best["ocr"],
            "boxes": _boxes(words, idxs, best["W"], best["H"]),
            "marks": _boxes(words, sorted(set(keys)), best["W"], best["H"], key=True)}


# ── Por tipo de archivo ───────────────────────────────────────────────────────
def highlight_pdf(path: str, chunks: List[dict], page_num: Optional[int], question: str, answer: str,
                  terms: List[str]) -> dict:
    import os
    if not page_num:
        page_num = next((c["page"] for c in chunks if c.get("page")), 1)
    doc = pymupdf.open(path)
    try:
        n_pages = len(doc)
        page_num = max(1, min(page_num, n_pages))
        images = []
        if any(c.get("kind") == "image" for c in chunks):
            images = _image_boxes(doc[page_num - 1])
    finally:
        doc.close()

    # 1) Pasaje exacto: la página citada + las páginas digitales (rápidas). Los escaneos solo si es la citada.
    pages = [page_num] + [p for p in range(1, n_pages + 1)
                          if p != page_num and (p in {c.get("page") for c in chunks} or _page_is_digital(path, p - 1))]
    found = best_passage(path, pages, page_num, question, answer) if (question or answer) else None
    if found and not (images and found["page"] != page_num):
        if found["page"] == page_num:
            found["boxes"] += images
        return {"type": "pdf", **found, "terms": terms}

    # 2) Respaldo: alinear el texto del fragmento recuperado
    words, W, H, ocr = _page_words_cached(path, os.path.getmtime(path), page_num - 1)
    marked = set()
    for c in chunks:
        if c.get("kind") != "image" and (not c.get("page") or c["page"] == page_num):
            marked.update(_align(words, c["text"]))
    keys = [i for i in marked if _is_key(norm(words[i][4]), terms)]
    return {"type": "pdf", "page": page_num, "ocr": ocr,
            "boxes": _boxes(words, list(marked), W, H) + images,
            "marks": _boxes(words, keys, W, H, key=True), "terms": terms}


@lru_cache(maxsize=32)
def _image_ocr(path: str, mtime: float):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((1600, 1600))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return im.width, im.height, _ocr_lines(buf.getvalue())


def highlight_image(path: str, chunks: List[dict], terms: List[str]) -> dict:
    import os
    W, H, lines = _image_ocr(path, os.path.getmtime(path))
    described = any(c.get("kind") == "image" for c in chunks)
    marks, boxes = [], []
    for (x0, y0, x1, y1), text in lines:
        ranks = [r for r in (_term_rank(t, terms) for t in tokens_of(text)) if r >= 0]
        if ranks:
            box = {"x": x0 / W, "y": y0 / H, "w": (x1 - x0) / W, "h": (y1 - y0) / H, "key": True, "text": text,
                   "rank": min(ranks)}
            marks.append(box)
    marks.sort(key=lambda m: m["rank"])
    marks = marks[:8]
    # misma fila (menús, tablas): los datos al lado de la palabra clave, p. ej. los precios
    for m in marks:
        cy0, cy1 = m["y"], m["y"] + m["h"]
        for (x0, y0, x1, y1), text in lines:
            ny0, ny1 = y0 / H, y1 / H
            center = (ny0 + ny1) / 2
            if cy0 <= center <= cy1 and x0 / W > m["x"] + m["w"] - 0.01:
                box = {"x": x0 / W, "y": ny0, "w": (x1 - x0) / W, "h": ny1 - ny0, "key": False, "row": True}
                if box not in boxes:
                    boxes.append(box)
    if not marks and any(c.get("kind") != "image" for c in chunks):
        # usó el texto de la imagen pero sin palabras clave claras: marca todo el texto leído
        boxes = [{"x": b[0] / W, "y": b[1] / H, "w": (b[2] - b[0]) / W, "h": (b[3] - b[1]) / H, "key": False}
                 for b, _ in lines]
    return {"type": "image", "whole": described, "boxes": boxes, "marks": marks, "terms": terms}


def highlight_media(chunks: List[dict], terms: List[str]) -> dict:
    segs = [{"chunk": c["chunk_index"], "start": c.get("start_sec") or 0, "end": c.get("end_sec") or c.get("start_sec") or 0,
             "kind": c.get("kind")} for c in chunks]
    return {"type": "media", "segments": segs, "terms": terms}


def highlight(doc, path: Optional[str], chunk_indexes: List[int], page: Optional[int], text: str,
              start_sec: Optional[float] = None, question: str = "", answer: str = "") -> dict:
    """Punto de entrada: decide qué marcar según el tipo de archivo.
    `text` = pregunta + respuesta (la pregunta primero: sus palabras pesan más)."""
    all_chunks = vector_store.get_chunks(doc.collection_name)
    chunks = [c for c in all_chunks if c["chunk_index"] in set(chunk_indexes or [])]
    media_type = doc.media_type or "pdf"
    if not chunks:                                   # historial antiguo: ubica por página o segundo
        if media_type in ("audio", "video") and start_sec is not None:
            chunks = [c for c in all_chunks if c["start_sec"] is not None and abs(c["start_sec"] - start_sec) < 0.6]
        elif page:
            chunks = [c for c in all_chunks if c["page"] == page]
    if not question and not answer and text:
        question, _, answer = text.partition("\n")
    text = f"{question}\n{answer}"
    terms = key_terms(text)
    if media_type in ("audio", "video"):
        return highlight_media(chunks, terms)
    if not path:
        return {"type": media_type, "boxes": [], "marks": [], "terms": terms, "missing": True}
    if media_type == "image":
        return highlight_image(path, chunks, terms)
    return highlight_pdf(path, chunks, page, question, answer, terms)
