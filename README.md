# Archivo Vivo · Pregúntale al archivo

Una **base de conocimiento** con **PDFs (con imágenes), imágenes sueltas, audios y videos cortos**, y una app web para hacerle **consultas semánticas** a todo ese material. La respuesta cita el **archivo y la página** (PDF) o el **archivo y el minuto** (audio o video); al tocar la cita, el visor abre esa página o el reproductor salta a ese momento.

Es la evolución del ChatPDF: ya no se le pregunta a un PDF a la vez, sino a toda una carpeta.

Proyecto de clase de **Daniel Guerrero** y **Sonny Arcos**.

---

## Requisitos del profesor y cómo se cumplen

| Requisito | En el proyecto |
| :--- | :--- |
| Partir de ChatPDF (se puede cambiar el nombre) | Ahora se llama **Archivo Vivo**; conserva login, roles, visor de PDF, resumen, explorador de fragmentos y chat |
| Base de conocimiento en una carpeta con audios, videos cortos y PDFs | Carpeta **`base_conocimiento/`** (subcarpetas `pdf/`, `imagenes/`, `audio/`, `video/`, `subidos/`). Se llena copiando archivos ahí y pulsando **"Indexar carpeta"**, o subiéndolos desde la web |
| App web con consulta semántica | Dashboard → modo **"Toda la base"**: la pregunta se busca por similitud coseno en todos los archivos (PostgreSQL + pgvector) |
| PDFs con imágenes, preguntas sobre texto e imágenes | El texto se indexa por página y cada imagen se describe con **`qwen2.5vl:3b`** (Ollama) y se guarda como fragmento `[Imagen en la página X]` |
| Embeddings con **EmbeddingGemma 2** | `backend/services/embedding_service.py` → vectores de **768 dimensiones** con los prefijos oficiales (`title: … \| text: …` para documentos y `task: search result \| query: …` para preguntas) |
| Usar **llama.cpp** | EmbeddingGemma 2 corre en **`llama-server`** de llama.cpp (puerto 8081, API `/v1/embeddings`). Además, Ollama (visión) también funciona sobre llama.cpp |
| Modelos locales o en la nube | **Locales:** EmbeddingGemma 2 (llama.cpp) y qwen2.5vl:3b (Ollama). **Nube (gratis):** Groq `llama-3.3-70b-versatile` para responder y **Whisper large-v3-turbo** para transcribir; Claude como respaldo opcional |

## Cómo funciona

**Al entrar un archivo** (`kb_service.py`):

| Tipo | Qué se extrae | Fragmentos |
| :--- | :--- | :--- |
| PDF (máx. 10 hojas) | Texto por página (OCR si es escaneado) + imágenes descritas por qwen2.5vl | ~220 palabras con solape de 40, con su **página** |
| Imagen (jpg, png, webp…) | Descripción detallada con qwen2.5vl + texto leído con OCR (RapidOCR) | `[Imagen]` y `[Texto en la imagen]`, citados como página 1 |
| Audio (máx. 20 min) | Transcripción con Whisper (con marcas de tiempo) | ~120 palabras con su **minuto de inicio y fin** |
| Video (máx. 20 min) | Transcripción del audio + **4 fotogramas** descritos por qwen2.5vl | Tramos de transcripción + `[Fotograma MM:SS]` |

Cada fragmento se vectoriza con **EmbeddingGemma 2** y va a una sola tabla de PostgreSQL:

```
kb_fragmentos(id, collection_name, chunk_index, page, start_sec, end_sec,
              kind ['text' | 'image' | 'transcript' | 'frame'], content, embedding vector(768))
índice HNSW con distancia coseno
```

**Chunks de marcado (de dónde salió la respuesta):** cada fuente de la respuesta trae el número de fragmento. Al tocar una cita (o automáticamente después de cada respuesta) el visor abre el archivo y `highlight_service.py` ubica ese fragmento en el original:

| Tipo | Cómo se marca |
| :--- | :--- |
| PDF digital | Las palabras de la página con sus coordenadas (PyMuPDF) se alinean con el texto del fragmento: el párrafo queda con resaltador amarillo y las palabras clave de la pregunta subrayadas en magenta |
| PDF escaneado | Igual, pero las palabras y sus posiciones salen del OCR (RapidOCR) |
| Imagen dentro de un PDF | Recuadro sobre la imagen en la página |
| Imagen suelta | Círculos de "lápiz graso" alrededor del texto encontrado con OCR (y la fila completa en menús o tablas); marco punteado si se usó la descripción |
| Audio / video | El tramo de la transcripción queda resaltado con sus palabras clave, el tramo aparece en la línea de tiempo y el reproductor salta a ese segundo |

**Escenas:** la interfaz cambia según el tipo de archivo con el que se conversa (botón "Este archivo"): **Imprenta** para PDF, **Moviola** (sala de montaje) para video, **Mesa de luz** con lupa para imágenes y **Estudio de radio** con ecualizador en vivo y dial de sintonía para audio. En "Toda la base" la pantalla es un collage: cada panel toma la escena de lo que muestra.

**Al preguntar:**
1. La pregunta se vectoriza con EmbeddingGemma 2 (prefijo de consulta).
2. pgvector busca los fragmentos más parecidos: en **toda la base** o solo en **el archivo abierto** (botones del chat).
3. Se descartan los poco relevantes y se arma el contexto con el nombre del archivo y su página o minuto.
4. Groq responde solo con ese contexto y cita `[archivo.pdf, página 3]` o `[clase.mp4, minuto 02:15]`.
5. En la app, las citas y las etiquetas de fuente abren el archivo en esa página o en ese momento.

---

## Cómo correrlo (Windows)

> El repositorio **no trae ninguna clave**: cada quien usa la suya. Sí incluye la base de conocimiento de ejemplo (PDFs, una imagen, un audio y un video) ya procesada, con la copia de sus vectores, así que se puede preguntar apenas arranca, sin volver a procesar nada.

### Requisitos (una sola vez)
1. **Python 3.12 o superior** – <https://www.python.org/downloads/> (marca *"Add python.exe to PATH"*).
2. **Docker Desktop** – <https://www.docker.com/products/docker-desktop/>
3. **Git** – <https://git-scm.com/download/win>
4. **llama.cpp** (para EmbeddingGemma 2). En PowerShell:
   ```powershell
   winget install --id ggml.llamacpp --exact --source winget
   ```
   Cierra y vuelve a abrir la ventana. La primera vez que `run.bat` lo arranque descarga el modelo `unsloth/embeddinggemma-2-GGUF` (unos 200 MB).
5. **Una clave de Groq** (gratis): entra a <https://console.groq.com>, crea una cuenta y en **API Keys** genera una clave (empieza por `gsk_`). Se usa para escribir las respuestas (Llama 3.3 70B) y para transcribir audios y videos (Whisper).
6. **Ollama** para leer imágenes y fotogramas – <https://ollama.com/download> y luego:
   ```powershell
   ollama pull qwen2.5vl:3b
   ```
   Sin Ollama todo funciona, pero sin describir imágenes ni fotogramas.

### Pasos
1. Clona el repositorio (`git clone` + el enlace de este repositorio) o descárgalo como ZIP.
2. Abre **Docker Desktop** (y **Ollama**).
3. Doble clic en **`run.bat`**. Hace todo solo:
   1. busca Python y, la primera vez, crea `backend/.env` y lo abre en el Bloc de notas para que pegues tu clave de Groq en `GROQ_API_KEY=` (guarda y cierra),
   2. enciende PostgreSQL + pgvector en Docker,
   3. enciende **llama.cpp con EmbeddingGemma 2** en el puerto 8081 (ventana minimizada),
   4. instala las dependencias,
   5. carga en pgvector la base de conocimiento (`migrar_a_pgvector.py`),
   6. abre <http://localhost:8000>.

Para comprobar todo: <http://localhost:8000/api/health> debe decir `"status": "healthy"` y mostrar `embeddings`, `vector_db`, `vision` y `knowledge_base`.

### Agregar archivos a la base
- **Desde la web:** "Agregar a la base" (quedan en `base_conocimiento/subidos/`).
- **Desde la carpeta:** copia PDFs, imágenes (jpg, png, webp…), audios (mp3, wav, m4a…) o videos (mp4, mov, webm…) en `base_conocimiento/` y pulsa **"Indexar carpeta"**. Indexa lo nuevo, vuelve a indexar lo que cambió y quita lo que se borró.

---

## Páginas

| Ruta | Qué es |
| :--- | :--- |
| `/` | Landing page (estilo "prueba de imprenta") |
| `/login` | Inicio de sesión y registro |
| `/dashboard` | La app: base de conocimiento, visor de PDF, reproductor con transcripción, resumen, fragmentos y chat |
| `/api/health` | Estado de llama.cpp, PostgreSQL, Ollama, Whisper y la carpeta |
| `/docs` | Documentación de la API (Swagger) |

## Cuentas de prueba

| Rol | Correo | Contraseña |
| :--- | :--- | :--- |
| Administrador (demostración) | `admin@chatpdf.com` | `admin123` |

Cualquiera puede crear su propia cuenta desde `/login`.

La base es **compartida**: todos ven y consultan los mismos archivos; solo quien subió un archivo (o un administrador) puede eliminarlo. El historial de chat es de cada usuario.

---

## Arquitectura

```
 Navegador ── landing.html · index.html · dashboard.html
    │          PDF.js · <video>/<audio> con transcripción · Marked · Mermaid
    ▼
 FastAPI (backend/main.py) ── JWT + roles (auth.py, admin.py)
    │
    ├── SQLite: usuarios, archivos de la base, historiales de chat
    │
    ├── kb_service.py ── carpeta base_conocimiento/ (subir · indexar carpeta)
    │      ├── pdf_service.py ── PyMuPDF + OCR + imágenes ──┐
    │      ├── media_service.py ── ffmpeg ─ Whisper (Groq) ─┤
    │      │                      └ fotogramas ─────────────┤
    │      │                vision_service.py → Ollama qwen2.5vl (llama.cpp)
    │      └── embedding_service.py → llama-server · EmbeddingGemma 2 (768 d)
    │                └── vector_store.py → PostgreSQL + pgvector (kb_fragmentos, HNSW)
    │
    └── rag_service.py ── búsqueda en pgvector → contexto → Groq → respuesta con citas
```

## Estructura

```
ArchivoVivo/
├── run.bat                       # Arranque: Docker + llama.cpp + dependencias + servidor
├── base_conocimiento/            # LA BASE: pdf/ · audio/ · video/ · subidos/
├── backend/
│   ├── .env.example              # Plantilla de configuración: run.bat crea .env a partir de ella
│   ├── chatpdf_expert.db         # Base de datos de prueba (solo el admin de demostración)
│   ├── main.py                   # API: archivos, chat por archivo, consulta a la base, indexación
│   ├── auth.py · admin.py        # Login JWT, roles y administración
│   ├── database.py · models.py   # SQLite (usuarios, archivos, chats)
│   ├── migrar_a_pgvector.py      # Carga la base en pgvector (y recalcula vectores viejos)
│   ├── exportar_vectores.py      # Copia los vectores de pgvector a chroma_data (antes de subir)
│   ├── chroma_data/              # Copia portable de fragmentos + vectores (json + npy)
│   └── services/
│       ├── kb_service.py         # Carpeta de la base, ingesta y sincronización
│       ├── embedding_service.py  # EmbeddingGemma 2 vía llama.cpp
│       ├── pdf_service.py        # Texto, OCR, imágenes y chunking de PDFs
│       ├── media_service.py      # Audio/video: ffmpeg, Whisper y fotogramas
│       ├── vision_service.py     # qwen2.5vl en Ollama
│       ├── highlight_service.py  # Chunks de marcado: ubica en el original lo que usó la respuesta
│       ├── vector_store.py       # PostgreSQL + pgvector
│       └── rag_service.py        # Búsqueda, contexto y respuesta con Groq
└── frontend/                     # landing.html · index.html · dashboard.html · assets/
                                  #   css/escenas.css = las 4 escenas (imprenta, moviola, mesa de luz, estudio de radio)
```

## Configuración (`backend/.env`)

| Variable | Para qué |
| :--- | :--- |
| `GROQ_API_KEY` | **Tu clave** para el LLM de respuestas y Whisper (gratis en <https://console.groq.com>). El archivo `.env` nunca se sube a GitHub |
| `LLAMACPP_EMBED_URL` | Dónde está `llama-server` (por defecto `http://127.0.0.1:8081`) |
| `TRANSCRIBE_BACKEND` | `groq` (nube, por defecto) o `local` (requiere `pip install faster-whisper`) |
| `MAX_MEDIA_MINUTES` · `VIDEO_FRAMES` · `MAX_PDF_PAGES` | Límites de la base (20 min, 4 fotogramas, 10 hojas) |
| `OLLAMA_VISION_MODEL` | Modelo de visión (por defecto `qwen2.5vl:3b`) |
| `PGVECTOR_URL` | PostgreSQL con pgvector |

## Antes de subir cambios a GitHub

PostgreSQL vive dentro de Docker y no viaja con el repositorio. Exporta los vectores antes de cada `git push`:

```powershell
cd backend
py exportar_vectores.py
cd ..
git add -A
git commit -m "describe tu cambio"
git push
```

En el otro PC, `run.bat` los carga con `migrar_a_pgvector.py` sin volver a procesar los archivos.

## Problemas comunes

- **"GROQ_API_KEY no está configurada"**: abre `backend/.env`, pega tu clave después de `GROQ_API_KEY=` (sin comillas ni espacios), guarda y vuelve a abrir `run.bat`.
- **"llama.cpp no está corriendo"**: abre `run.bat` de nuevo o, en otra ventana: `llama-server -hf unsloth/embeddinggemma-2-GGUF:UD-Q4_K_XL --embeddings --port 8081 --device none -ngl 0`.
- **"No se pudo conectar a PostgreSQL"**: Docker Desktop está cerrado. Ábrelo y ejecuta `run.bat` otra vez.
- **Una imagen sin texto no se indexa**: abre Ollama; sin el modelo de visión solo se puede leer el texto de la imagen con OCR.
- **Las imágenes o fotogramas no se describen**: Ollama está cerrado o falta `ollama pull qwen2.5vl:3b`.
- **Un audio largo falla al transcribir**: el máximo es 20 minutos por archivo.
- **El navegador muestra la versión vieja**: recarga con **Ctrl + F5**.
