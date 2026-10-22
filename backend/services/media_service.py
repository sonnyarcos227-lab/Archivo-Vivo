"""
media_service.py – Audios y videos cortos → fragmentos buscables.

  • Audio: se extrae/comprime con ffmpeg y se transcribe con Whisper
    (whisper-large-v3-turbo en Groq por defecto; o faster-whisper local si se
    configura TRANSCRIBE_BACKEND=local). Cada fragmento lleva su marca de tiempo.
  • Video: lo mismo para el audio + algunos fotogramas descritos por el modelo
    de visión qwen2.5vl (Ollama). Así se puede preguntar por lo que se dice y
    por lo que se ve.

ffmpeg viene incluido con el paquete de Python imageio-ffmpeg (no hay que
instalarlo aparte).
"""

import os
import re
import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from dotenv import load_dotenv

try:
    from services import vision_service
except ImportError:
    import vision_service

load_dotenv()
_backend_env = Path(__file__).resolve().parent.parent / ".env"
if _backend_env.exists():
    load_dotenv(dotenv_path=_backend_env)

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".wma"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

TRANSCRIBE_BACKEND = os.getenv("TRANSCRIBE_BACKEND", "groq").lower()        # groq | local
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
LOCAL_WHISPER_MODEL = os.getenv("LOCAL_WHISPER_MODEL", "small")
MEDIA_LANGUAGE = os.getenv("MEDIA_LANGUAGE", "es")
MAX_MEDIA_MINUTES = float(os.getenv("MAX_MEDIA_MINUTES", "20"))
VIDEO_FRAMES = int(os.getenv("VIDEO_FRAMES", "4"))
CHUNK_WORDS = int(os.getenv("MEDIA_CHUNK_WORDS", "120"))


class MediaError(RuntimeError):
    pass


def media_type_for(filename: str) -> Optional[str]:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return None


def fmt_time(sec: Optional[float]) -> str:
    sec = int(round(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ── ffmpeg ────────────────────────────────────────────────────────────────────
def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"   # si está instalado en el sistema


def _run(args: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW en Windows
    return subprocess.run([_ffmpeg(), "-hide_banner", *args], capture_output=True, timeout=timeout,
                          creationflags=flags)


def probe(path: str) -> Tuple[float, bool]:
    """(duración en segundos, tiene_video)."""
    proc = _run(["-i", path], timeout=60)
    info = proc.stderr.decode("utf-8", errors="ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if not m:
        raise MediaError("No se pudo leer el archivo de audio/video (¿está dañado o en un formato raro?).")
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    has_video = any("Video:" in line and "attached pic" not in line
                    for line in info.splitlines() if line.strip().startswith("Stream #"))
    return dur, has_video


def extract_audio(path: str, out_dir: str) -> str:
    """Audio mono 16 kHz en mp3 de 48 kbps (≈ 21 MB por hora: cabe en el límite de Groq)."""
    out = os.path.join(out_dir, "audio.mp3")
    proc = _run(["-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", out])
    if proc.returncode != 0 or not os.path.exists(out):
        raise MediaError("El archivo no tiene una pista de audio que se pueda extraer.")
    return out


def extract_frame(path: str, at_sec: float, out_dir: str, idx: int) -> Optional[bytes]:
    out = os.path.join(out_dir, f"frame_{idx}.png")
    proc = _run(["-y", "-ss", f"{at_sec:.2f}", "-i", path, "-frames:v", "1", "-vf", "scale='min(1024,iw)':-2", out], timeout=120)
    if proc.returncode == 0 and os.path.exists(out):
        with open(out, "rb") as f:
            return f.read()
    return None


# ── Transcripción ─────────────────────────────────────────────────────────────
def _transcribe_groq(audio_path: str) -> List[dict]:
    from groq import Groq
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        raise MediaError("Falta GROQ_API_KEY en backend/.env para transcribir con Whisper.")
    client = Groq(api_key=key)
    with open(audio_path, "rb") as f:
        resp = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f.read()),
            model=GROQ_WHISPER_MODEL,
            language=MEDIA_LANGUAGE,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
            temperature=0.0,
        )
    data = resp if isinstance(resp, dict) else (resp.model_dump() if hasattr(resp, "model_dump") else json.loads(resp.json()))
    segs = data.get("segments") or []
    if not segs and data.get("text"):
        segs = [{"start": 0.0, "end": data.get("duration") or 0.0, "text": data["text"]}]
    return [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": (s.get("text") or "").strip()} for s in segs]


def _transcribe_local(audio_path: str) -> List[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise MediaError("TRANSCRIBE_BACKEND=local requiere: pip install faster-whisper") from e
    model = WhisperModel(LOCAL_WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, language=MEDIA_LANGUAGE, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]


def transcribe(audio_path: str) -> List[dict]:
    if TRANSCRIBE_BACKEND == "local":
        return _transcribe_local(audio_path)
    return _transcribe_groq(audio_path)


def group_segments(segments: List[dict], start_index: int = 0) -> List[dict]:
    """Une segmentos de Whisper en fragmentos de ~120 palabras con su rango de tiempo."""
    chunks, buf, words, t0 = [], [], 0, None
    for seg in segments:
        if not seg["text"]:
            continue
        if t0 is None:
            t0 = seg["start"]
        buf.append(seg["text"])
        words += len(seg["text"].split())
        t1 = seg["end"]
        if words >= CHUNK_WORDS:
            chunks.append((t0, t1, " ".join(buf)))
            buf, words, t0 = [], 0, None
    if buf:
        chunks.append((t0, t1, " ".join(buf)))
    return [{
        "text": f"[{fmt_time(a)}–{fmt_time(b)}] {txt}",
        "start_sec": round(a, 2), "end_sec": round(b, 2),
        "kind": "transcript", "chunk_index": start_index + i, "page": None,
    } for i, (a, b, txt) in enumerate(chunks)]


# ── Pipeline principal ────────────────────────────────────────────────────────
def process_media(path: str, progress=None) -> Tuple[List[dict], float, str]:
    """
    Convierte un audio o video en fragmentos.
    Devuelve (fragmentos, duración_en_segundos, media_type).
    """
    say = progress or (lambda msg: print(f"[INFO] {msg}"))
    duration, has_video = probe(path)
    if duration > MAX_MEDIA_MINUTES * 60:
        raise MediaError(f"El archivo dura {fmt_time(duration)}; el máximo es {int(MAX_MEDIA_MINUTES)} minutos.")
    kind = "video" if has_video and media_type_for(path) != "audio" else "audio"
    chunks: List[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        say("Extrayendo el audio…")
        try:
            audio = extract_audio(path, tmp)
            say(f"Transcribiendo con Whisper ({'Groq' if TRANSCRIBE_BACKEND != 'local' else 'local'})…")
            segments = transcribe(audio)
            chunks.extend(group_segments(segments))
        except MediaError as e:
            if kind == "audio":
                raise
            print(f"[WARN] Video sin audio utilizable: {e}")

        if kind == "video" and VIDEO_FRAMES > 0 and vision_service.is_available():
            times = [duration * (i + 0.5) / VIDEO_FRAMES for i in range(VIDEO_FRAMES)]
            for i, t in enumerate(times, 1):
                say(f"Describiendo fotograma {i}/{len(times)} con {vision_service.VISION_MODEL}…")
                png = extract_frame(path, t, tmp, i)
                if not png:
                    continue
                desc = vision_service.describe_image(png, keep_alive="0s" if i == len(times) else "60s")
                if desc:
                    chunks.append({
                        "text": f"[Fotograma {fmt_time(t)}] {desc}",
                        "start_sec": round(t, 2), "end_sec": round(t, 2),
                        "kind": "frame", "chunk_index": len(chunks), "page": None,
                    })
        elif kind == "video":
            print("[INFO] Modelo de visión no disponible: el video se indexa solo con su audio.")

    if not chunks:
        raise MediaError("No se encontró contenido para indexar (sin voz detectable ni fotogramas descritos).")
    chunks.sort(key=lambda c: (c["start_sec"] or 0, c["kind"] != "transcript"))
    for i, c in enumerate(chunks):
        c["chunk_index"] = i
    return chunks, duration, kind


def status() -> dict:
    try:
        import imageio_ffmpeg  # noqa: F401
        ff = "imageio-ffmpeg"
    except Exception:
        ff = "sistema (no instalado vía pip)"
    return {
        "transcription": f"Whisper · {GROQ_WHISPER_MODEL} (Groq)" if TRANSCRIBE_BACKEND != "local"
                         else f"Whisper local · faster-whisper {LOCAL_WHISPER_MODEL}",
        "ffmpeg": ff, "video_frames": VIDEO_FRAMES, "max_minutes": MAX_MEDIA_MINUTES,
    }
